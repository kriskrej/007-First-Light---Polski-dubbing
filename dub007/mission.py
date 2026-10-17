"""Read-only preparation of the first mission; no paid generation here."""
from collections import Counter
import json
import re

from .archive import open_index, parse_tables, read_resource
from .common import ARTIFACTS, ROOT, save_json, sha256
from .selection import metadata, riff_metadata, native_labels as read_native_labels


def run(mission_id="clover", chapter_id=None, *, native_labels=None, native_only=False,
        audio_path_prefixes=None, dialogue_path_prefixes=None):
    if not re.fullmatch(r"[a-z0-9_-]+", mission_id) or (chapter_id and not re.fullmatch(r"[a-z0-9-]+", chapter_id)):
        raise ValueError("Invalid mission or chapter identifier")
    folder = ARTIFACTS / "chapters" / chapter_id if chapter_id else ARTIFACTS / "first-mission"
    existing_path = folder / "manifest.json"
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if any(row.get("generation_status") != "not_started" for row in existing.get("rows", [])):
            raise RuntimeError("Mission generation has started; refusing to overwrite its manifest")
    audio_paths = json.loads((ROOT / "research/bond-hashes/paths/WWES.json").read_text(encoding="utf-8"))
    clover_audio = {row["hash"]: row["path"] for row in audio_paths if f"/voices/english(us)/{mission_id}/" in row["path"]}
    if audio_path_prefixes is not None:
        clover_audio = {h: path for h, path in clover_audio.items() if any(path.startswith(p) for p in audio_path_prefixes)}
    dialogue_paths = json.loads((ROOT / "research/bond-hashes/paths/DLGE.json").read_text(encoding="utf-8"))
    clover_dialogues = {row["hash"]: row["path"] for row in dialogue_paths if f"/conversations/{mission_id}/" in row["path"]}
    if dialogue_path_prefixes is not None:
        clover_dialogues = {h: path for h, path in clover_dialogues.items() if any(path.startswith(p) for p in dialogue_path_prefixes)}
    native_labels = native_labels or {}
    if native_only:
        clover_audio, clover_dialogues = {}, {}
    records, unresolved, uses = {}, [], {}
    with (ARTIFACTS / "texts/dialogue-mapped.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            audio_hash = row.get("audio_hash_en")
            if audio_hash not in clover_audio and row["line_id"] not in clover_dialogues and audio_hash not in native_labels:
                continue
            if row.get("status") == "parse_failed":
                unresolved.append(row)
                continue
            if not audio_hash:
                continue
            signature = (row["speaker_id"], row["text_en"], row["text_pl_original"])
            if audio_hash in records:
                previous = records[audio_hash]
                if signature != (previous["speaker_id"], previous["text_en"], previous["text_pl_original"]):
                    raise ValueError("First-mission audio has conflicting dialogue uses")
            else:
                records[audio_hash] = {**row, "audio_source_path": clover_audio.get(audio_hash),
                                       "dialogue_source_path": clover_dialogues.get(row["line_id"]),
                                       "native_audio_label": native_labels.get(audio_hash, {}).get("label")}
            uses.setdefault(audio_hash, []).append({"line_id": row["line_id"], "leaf_index": row["leaf_index"], "archive": row["archive"]})
    original_folder = folder / "originals"
    original_folder.mkdir(parents=True, exist_ok=True)
    database = open_index()
    for archive in {row["archive"] for row in records.values()}:
        cached = database.execute("SELECT fingerprint FROM archives WHERE name=?", (archive,)).fetchone()
        if not cached or json.loads(cached[0]) != parse_tables(ROOT / "Runtime" / archive)[-1]:
            database.close()
            raise ValueError("Mission source archive changed since index creation")
    streams, codecs, rates = {}, Counter(), Counter()
    vgm = ROOT / "research/vgmstream-win64/vgmstream-cli.exe"
    try:
        for audio_hash, row in records.items():
            entries = database.execute("SELECT * FROM resources WHERE hash=? AND type='WWES'", (audio_hash,)).fetchall()
            if len(entries) != 1:
                raise ValueError("First-mission WWES archive placement is ambiguous")
            entry = entries[0]
            archive = entry["archive"]
            if archive not in streams:
                streams[archive] = (ROOT / "Runtime" / archive).open("rb")
            payload = read_resource(streams[archive], entry)
            wem = original_folder / f"{audio_hash}.wem"
            digest = sha256(payload)
            if audio_hash in native_labels and native_labels[audio_hash]["original_payload_sha256"] != digest:
                raise ValueError("Native label audit no longer matches the original resource")
            if not wem.exists() or sha256(wem.read_bytes()) != digest:
                wem.write_bytes(payload)
            measured = metadata(vgm, wem)
            row.update(riff_metadata(payload))
            row.update(measured)
            if row.get("audio_variant_count", 1) > 1:
                labels = {item["label"] for item in read_native_labels(row["original_markers"])}
                if len(labels) != 1:
                    raise ValueError("Audio switch variant needs one unambiguous native label")
                row["native_audio_label"] = next(iter(labels))
                mode = re.search(r"_(combat|regular|stealth)_?$", row["native_audio_label"])
                if mode:
                    row["native_delivery_variant"] = mode[1]
                    row["tts_audio_tag"] = {"combat": "urgent", "regular": "snappy", "stealth": "whispers"}[mode[1]]
            row.update(active_archive=archive, original_payload_sha256=digest, raw_resource_refs_hex=entry["refs"].hex(),
                       sysmem=entry["sysmem"], vidmem=entry["vidmem"], original_wem_path=str(wem.relative_to(ROOT)),
                       text_pl_display=row["text_pl_original"], text_pl_tts=row["text_pl_original"], uses=uses[audio_hash],
                       generation_status="not_started", install_status="not_installed", qa_status="original_metadata_verified")
            codecs[row["original_codec"]] += 1
            rates[str(row["sample_rate"])] += 1
    finally:
        for stream in streams.values():
            stream.close()
        database.close()
    rows = list(records.values())
    casting_path = ROOT / "config" / ("campaign-casting.json" if chapter_id else "first-mission-casting.json")
    casting = json.loads(casting_path.read_text(encoding="utf-8")) if casting_path.exists() else {"speakers": {}}
    for row in rows:
        row.update(voice_id=casting["speakers"].get(row["speaker_id"], {}).get("voice_id"),
                   model_id=casting.get("model_id"), settings=casting.get("settings"),
                   generated_audio_path=None, wem_path=None, wem_sha256=None,
                   segment_ids=[segment["segment_id"] for segment in row["segments_en"]])
    save_json(folder / "manifest.json", {"status": "mapped_not_generated", "chapter_id": chapter_id,
                                        "mission_id": mission_id, "rows": rows, "unresolved": unresolved})
    pl_chars = sum(len(row["text_pl_original"]) for row in rows)
    poc_path = ARTIFACTS / "poc/manifest.json"
    poc_hashes = {row["audio_hash_en"] for row in json.loads(poc_path.read_text(encoding="utf-8"))["rows"]
                  if row.get("generation_status") == "generated"} if poc_path.exists() else set()
    poc_done = sum(row["audio_hash_en"] in poc_hashes for row in rows)
    nbp = json.loads((ARTIFACTS / "nbp-usd.json").read_text(encoding="utf-8")) if (ARTIFACTS / "nbp-usd.json").exists() else None
    fx = nbp["rates"][0]["mid"] if nbp else 4
    report = {"status": "mapped_not_generated", "candidate_scope": f"{mission_id} WWES paths plus local DLGE EN audio references",
              "known_en_audio_paths": len(clover_audio), "known_clover_dlge_paths": len(clover_dialogues),
              "unique_mapped_audio": len(rows), "with_pl": sum(bool(row["text_pl_original"]) for row in rows),
              "pl_chars": pl_chars, "missing_pl_audio": [row["audio_hash_en"] for row in rows if not row["text_pl_original"]],
              "speakers": Counter(row["speaker_id"] for row in rows), "codec_counts": codecs, "sample_rate_counts": rates,
              "original_seconds": sum(row["duration"] for row in rows), "unresolved_known_clover_dlge": unresolved,
              "poc_audio_lines_already_generated": poc_done, "additional_audio_lines": len(rows) - poc_done,
              "estimate": {"usd_per_1000": 0.08, "usd_pln": fx, "tax_assumed": 0.23, "reserve": 0.30,
                           "pln_with_assumed_tax_and_reserve": round(pl_chars * 1.3 / 1000 * 0.08 * fx * 1.23, 2)},
              "runtime_verified": False,
              "scope_limitations": ["Paths are scene hints; resource links were read from local DLGE",
                                    "Shared generic combat/ambient voices require mission dependency analysis",
                                    "Full-mission generation starts after PoC engine validation; no extra paid requests here"]}
    if chapter_id:
        report.update(chapter_id=chapter_id, mission_id=mission_id)
        save_json(ROOT / "docs/chapters" / f"{chapter_id}.json", report)
    else:
        save_json(ROOT / "docs/FIRST_MISSION.json", report)
    return report
