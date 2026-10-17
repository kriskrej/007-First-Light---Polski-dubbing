"""First Light leaves, official PL captions and audio-reference mapping."""
from collections import Counter
from contextlib import ExitStack
import json

from .archive import ensure_index, read_resource, refs
from .common import ARTIFACTS, ROOT, save_json, sha256
from .dlge import parse

VERSION = 6


def run(limit=None, baseline=False):
    database, inventory = ensure_index(baseline)
    destination = ARTIFACTS / "texts" / "dialogue-mapped.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256(json.dumps({key: value["index_sha256"] for key, value in inventory.items()}, sort_keys=True).encode())
    state_path = destination.with_suffix(".state.json")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if destination.exists() and state.get("fingerprint") == fingerprint and state.get("complete") and state.get("parser_version") == VERSION and limit is None:
        database.close()
        return state
    successes, failures, leaves_count = 0, 0, 0
    segment_count_en = segment_count_pl = characters_en = characters_pl = 0
    speakers, audio, conflicts, failure_reasons = set(), {}, {}, Counter()
    source_audio = {}
    audio_types = {row["hash"]: row["type"] for row in database.execute("SELECT hash,type FROM resources WHERE type IN ('WWES','WWEM')")}
    query = "SELECT * FROM resources WHERE type='DLGE' ORDER BY archive,offset"
    if limit:
        query += f" LIMIT {int(limit)}"
    temporary = destination.with_name(destination.name + ".tmp")
    sample_folder = ARTIFACTS / "source" / "dlge-samples"
    sample_folder.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        streams = {archive: stack.enter_context((ROOT / "Runtime" / archive).open("rb")) for archive in inventory}
        output = stack.enter_context(temporary.open("w", encoding="utf-8", newline="\n"))
        for entry in database.execute(query):
            try:
                raw = read_resource(streams[entry["archive"]], entry)
                references = refs(entry)
                parsed = parse(raw, references)
                digest = sha256(raw)
                successes += 1
                if successes <= 12:
                    (sample_folder / f"{entry['hash']}.DLGE.bin").write_bytes(raw)
                for leaf in parsed["leaves"]:
                    en, pl = leaf["languages"]["en"], leaf["languages"]["pl"]
                    source = leaf["languages"]["source"]
                    audio_hash = en["audio_hash"]
                    resource_type = audio_types.get(audio_hash)
                    mapped = resource_type in ("WWES", "WWEM")
                    text_en = " ".join(segment["text"] for segment in en["segments"])
                    text_pl = " ".join(segment["text"] for segment in pl["segments"])
                    record = {"line_id": entry["hash"], "leaf_index": leaf["leaf_index"],
                              "archive": entry["archive"], "speaker_id": en["speaker_id"] or source["speaker_id"],
                              "speaker_name": en["speaker_name"] or source["speaker_name"],
                              "text_en": text_en, "text_pl_original": text_pl,
                              "segments_en": en["segments"], "segments_pl": pl["segments"],
                              "audio_hash_en": audio_hash, "resource_type": resource_type,
                              "audio_mapping_status": "reference_verified" if mapped else "missing_active_audio",
                              "translation_source": "game_pl" if text_pl else "missing_pl",
                              "audio_ref_index_en": en["audio_ref_index"], "face_ref_index_en": en["face_ref_index"],
                              "source_fields_raw": source["fields_raw"],
                              "text_raw_en": en["text_raw"], "text_raw_pl": pl["text_raw"],
                              "text_raw_source": source["text_raw"], "segments_source": source["segments"],
                              "text_source_script": " ".join(segment["text"] for segment in source["segments"]),
                              "original_dlge_payload_sha256": digest,
                              "root_typed_index": parsed["root_typed_index"],
                              "leaf_opaque_metadata": {key: leaf[key] for key in
                                                       ("offset", "soundtag_hash", "wav_name_hash", "opaque_u32", "opaque_u8")},
                              "listening_qa_status": "pending"}
                    record["leaf_raw_header_hex"] = leaf["raw_header_hex"]
                    record["leaf_switch_metadata"] = leaf["switch_metadata"]
                    variants = [(index, references[index]["hash"]) for index in en["audio_ref_indices"]
                                if index != 0xFFFFFFFF] or [(0xFFFFFFFF, None)]
                    for variant_index, (ref_index, variant_hash) in enumerate(variants):
                        variant_type = audio_types.get(variant_hash)
                        variant_record = {**record, "audio_hash_en": variant_hash,
                                          "audio_ref_index_en": ref_index, "resource_type": variant_type,
                                          "audio_mapping_status": "reference_verified" if variant_type in ("WWES", "WWEM") else "missing_active_audio",
                                          "audio_variant_index": variant_index, "audio_variant_count": len(variants),
                                          "audio_variant_hashes_en": en["audio_hashes"]}
                        output.write(json.dumps(variant_record, ensure_ascii=False) + "\n")
                        if variant_type in ("WWES", "WWEM"):
                            source_audio.setdefault(variant_hash, record["text_source_script"])
                            signature = (record["speaker_id"], text_en, text_pl)
                            if variant_hash in audio and audio[variant_hash] != signature:
                                conflicts.setdefault(variant_hash, set()).add(signature)
                                conflicts[variant_hash].add(audio[variant_hash])
                            else:
                                audio[variant_hash] = signature
                    leaves_count += 1
                    segment_count_en += len(en["segments"])
                    segment_count_pl += len(pl["segments"])
                    characters_en += len(text_en)
                    characters_pl += len(text_pl)
                    if record["speaker_id"]:
                        speakers.add(record["speaker_id"])
            except Exception as error:
                failures += 1
                reason = str(error)
                failure_reasons[reason.split(" at ")[0]] += 1
                output.write(json.dumps({"line_id": entry["hash"], "archive": entry["archive"],
                                         "status": "parse_failed", "error_type": type(error).__name__, "error": reason}) + "\n")
            if (successes + failures) % 1000 == 0:
                print(f"DLGE: {successes} parsed, {failures} unresolved; {len(audio)} unique EN audio references", flush=True)
    temporary.replace(destination)
    state = {"fingerprint": fingerprint, "parser_version": VERSION, "complete": limit is None,
             "parsed_dlge_resources": successes, "unresolved_dlge_resources": failures,
             "wav_leaf_uses": leaves_count, "subtitle_segments_en_before_audio_dedup": segment_count_en,
             "subtitle_segments_pl_before_audio_dedup": segment_count_pl,
             "characters_en_before_audio_dedup": characters_en,
             "characters_pl_before_audio_dedup": characters_pl,
             "unique_active_en_audio_references": len(audio),
             "unique_audio_with_pl_text": sum(bool(signature[2]) for signature in audio.values()),
             "characters_en_after_audio_dedup": sum(len(signature[1]) for signature in audio.values()),
             "characters_pl_after_audio_dedup": sum(len(signature[2]) for signature in audio.values()),
             "audio_text_or_speaker_conflicts": len(conflicts),
             "conflicted_audio_hashes": sorted(conflicts),
             "speaker_codes": len(speakers), "failure_reasons": dict(failure_reasons),
             "audio_mapping_status": "verified_reference_indices_pending_listening_qa",
             "source_script_characters_after_audio_dedup": sum(len(text) for text in source_audio.values()),
             "unique_audio_with_source_script": sum(bool(text) for text in source_audio.values()),
             "original_baseline_audit": baseline}
    save_json(state_path, state)
    save_json(ROOT / "docs" / "CORPUS.json", state)
    spoken = [signature for signature in audio.values() if signature[1] or signature[2]]
    missing_pl = [signature for signature in spoken if not signature[2]]
    save_json(ROOT / "docs" / "COVERAGE.json", {
        "unique_audio_with_en_or_pl_text": len(spoken),
        "unique_audio_with_missing_pl_text": len(missing_pl),
        "missing_pl_english_characters": sum(len(signature[1]) for signature in missing_pl),
        "audio_without_en_or_pl_text": len(audio) - len(spoken),
        "spoken_speaker_codes": len({signature[0] for signature in spoken if signature[0]}),
        "corpus_fingerprint": fingerprint, "complete_scan": limit is None})
    database.close()
    return state
