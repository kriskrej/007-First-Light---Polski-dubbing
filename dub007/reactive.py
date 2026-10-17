"""Prepare reviewed gameplay reactions using both audio and dialogue paths."""
from contextlib import ExitStack
import json
import re

from .common import ARTIFACTS, ROOT, save_json, sha256


def matches_scope(row, audio_path, dialogue_path, configuration):
    speaker = configuration.get("reactive_speaker")
    prefix = configuration.get("reactive_audio_prefix", "")
    dialogue_prefix = configuration.get("reactive_dialogue_prefix", "")
    tokens = configuration.get("reactive_location_tokens", [])
    if not speaker or not prefix or not dialogue_prefix or not tokens:
        return False
    pattern = re.compile(r"(?:/|_)(?:" + "|".join(map(re.escape, tokens)) + r")(?=/|_|\.)")
    return (row.get("speaker_id") == speaker
            and audio_path.startswith(prefix) and dialogue_path.startswith(dialogue_prefix)
            and bool(pattern.search(audio_path)) and bool(pattern.search(dialogue_path)))


def scene_key(row, configuration):
    if not matches_scope(row, row.get("audio_source_path", ""),
                         row.get("dialogue_source_path", ""), configuration):
        raise ValueError("Reactive resource falls outside the reviewed scope")
    label = row.get("native_audio_label", "")
    basename = row["audio_source_path"].rsplit("/", 1)[-1].split(".wav", 1)[0]
    if label != basename:
        raise ValueError("Reactive native label does not match its source audio path")
    match = re.fullmatch(r"(.+)_" + re.escape(row["speaker_id"].lower())
                         + r"_(\d{3})(?:_alt\d+)?", label)
    if not match:
        raise ValueError("Unreviewed reactive label layout")
    # Zero is only a library sorting key, never an inferred native scene number.
    return 0, match[1], int(match[2]), row["audio_hash_en"]


def run(scope="late-bond-reactive"):
    from .archive import open_index, parse_tables, read_resource
    from .campaign import chapter_config
    from .mission import run as prepare_mission
    from .selection import native_labels, riff_metadata

    configuration = chapter_config()["chapters"].get(scope, {})
    if configuration.get("scope_kind") != "reactive":
        raise ValueError("Unknown reviewed reactive scope")
    manifest_path = ARTIFACTS / "chapters" / scope / "manifest.json"
    if manifest_path.exists():
        return {"status": "prepared_manifest_reused", "scope": scope,
                "resources": len(json.loads(manifest_path.read_text(encoding="utf-8"))["rows"])}
    audio_paths = {r["hash"]: r["path"] for r in json.loads(
        (ROOT / "research/bond-hashes/paths/WWES.json").read_text(encoding="utf-8"))}
    dialogue_paths = {r["hash"]: r["path"] for r in json.loads(
        (ROOT / "research/bond-hashes/paths/DLGE.json").read_text(encoding="utf-8"))}
    selected = {}
    with (ARTIFACTS / "texts/dialogue-mapped.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            h = row.get("audio_hash_en")
            if matches_scope(row, audio_paths.get(h, ""), dialogue_paths.get(row["line_id"], ""), configuration):
                if row.get("resource_type") != "WWES" or not row.get("text_en") or not row.get("text_pl_original"):
                    raise ValueError("Reactive resource requires source/translation review")
                selected[h] = row
    evidence, review = {}, []
    index = open_index()
    try:
        with ExitStack() as stack:
            streams = {}
            for h, row in selected.items():
                entries = index.execute("SELECT * FROM resources WHERE hash=? AND type='WWES'", (h,)).fetchall()
                if len(entries) != 1:
                    raise ValueError("Ambiguous reactive audio placement")
                entry = entries[0]
                archive = entry["archive"]
                if archive not in streams:
                    saved = index.execute("SELECT fingerprint FROM archives WHERE name=?", (archive,)).fetchone()
                    if not saved or parse_tables(ROOT / "Runtime" / archive)[-1] != json.loads(saved[0]):
                        raise ValueError("Reactive source archive changed since indexing")
                    streams[archive] = stack.enter_context((ROOT / "Runtime" / archive).open("rb"))
                raw = read_resource(streams[archive], entry)
                labels = {item["label"] for item in native_labels(riff_metadata(raw)["original_markers"])}
                expected = audio_paths[h].rsplit("/", 1)[-1].split(".wav", 1)[0]
                resource_labels = {label for label in labels if re.fullmatch(r"[a-z0-9_]+_\d{3}(?:_alt\d+)?", label)}
                if expected not in labels or resource_labels != {expected}:
                    review.append({"audio_hash": h, "labels": sorted(labels),
                                   "reason": "Needs native-label review before generation"})
                    continue
                label = expected
                scene_key({**row, "native_audio_label": label, "audio_source_path": audio_paths[h],
                           "dialogue_source_path": dialogue_paths[row["line_id"]]}, configuration)
                evidence[h] = {"label": label, "original_payload_sha256": sha256(raw), "archive": archive}
    finally:
        index.close()
    save_json(ROOT / "docs/chapters" / f"{scope}-mapping.json", {
        "scope": scope, "ordering": "Library label order; these are player-triggered reactions, not story chronology",
        "evidence": "Local EN DLGE audio references, reviewed audio/dialogue location tokens and exact WEM LIST labels",
        "selection": evidence, "needs_review": review, "runtime_verified": False})
    report = prepare_mission("ai_dialog", scope, native_labels=evidence, native_only=True)
    prepared = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in prepared["rows"]:
        row.update(audio_source_path=audio_paths[row["audio_hash_en"]],
                   dialogue_source_path=dialogue_paths[row["line_id"]], reactive_scope=scope,
                   processing_order="library_label_not_story_chronology")
    save_json(manifest_path, prepared)
    return report
