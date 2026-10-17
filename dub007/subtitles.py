"""PoC subtitle edits limited to the Polish leaf corresponding to changed speech."""
import re

from .archive import open_index, read_resource, refs
from .common import ARTIFACTS, ROOT, sha256
from .dlge import replace_polish, text_fields, parse


def display_parts(row):
    if row.get("display_segments"):
        return row["display_segments"]
    segments = row.get("segments_pl") or []
    if segments and " ".join(segment["text"] for segment in segments) == row["text_pl_display"]:
        return [segment["text"] for segment in segments]
    return [row["text_pl_display"]]


def replacement_text(row, original, desired):
    timings = list(re.finditer(r"//\(([^)]*)\)\\\\", original))
    reviewed = row.get("reviewed_subtitle_segments")
    missing_pl = (not timings and not row.get("text_pl_original")
                  and row.get("translation_source") == "agent_en_fallback_missing_pl"
                  and bool(reviewed) and bool(text_fields(original)["fields_raw"]))
    if not timings and not missing_pl:
        raise ValueError("Original subtitle has no timing fields")
    if reviewed is None:
        if len(timings) != len(desired):
            raise ValueError("PL segment count must be preserved")
        return original[:timings[0].start()] + "".join(timing.group(0) + text for timing, text in zip(timings, desired))
    if not row.get("chapter_id") or not row.get("subtitle_timing_override_reason") or len(reviewed) != len(desired):
        raise ValueError("Subtitle timing change requires an explicit chapter review")
    previous_end = -1
    for segment in reviewed:
        start, end = map(float, segment["timing_raw"].split(","))
        if start < 0 or start < previous_end or end <= start or end > row["duration"] + 30:
            raise ValueError("Reviewed subtitle timing is invalid or overlaps")
        previous_end = end
    prefix = original[:timings[0].start()] if timings else original
    return prefix + "".join(
        f"//({segment['timing_raw']})" + chr(92) * 2 + text for segment, text in zip(reviewed, desired))


def resource(row):
    if row["text_pl_display"] == row["text_pl_original"] and row.get("reviewed_subtitle_segments") is None:
        return None
    database = open_index()
    entries = database.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", (row["line_id"],)).fetchall()
    database.close()
    if len(entries) != 1 or entries[0]["archive"] != row["archive"]:
        raise ValueError("Subtitle DLGE placement is ambiguous")
    entry = entries[0]
    with (ROOT / "Runtime" / entry["archive"]).open("rb") as stream:
        raw = read_resource(stream, entry)
    if sha256(raw) != row["original_dlge_payload_sha256"]:
        raise ValueError("Original subtitle payload checksum mismatch")
    segments = display_parts(row)
    new_text = replacement_text(row, row["text_raw_pl"], segments)
    references = refs(entry)
    payload = replace_polish(raw, references, row["leaf_index"], row["text_raw_pl"], new_text)
    folder = ARTIFACTS / "poc" / "subtitles"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{row['line_id']}.original.DLGE").write_bytes(raw)
    (folder / f"{row['line_id']}.DLGE").write_bytes(payload)
    row["subtitle_patch"] = {"resource_hash": row["line_id"], "original_payload_sha256": sha256(raw),
                             "payload_sha256": sha256(payload), "only_language": "pl",
                             "segment_timings_unchanged": not bool(row.get("reviewed_subtitle_segments")),
                             "runtime_verified": False}
    return {"hash": row["line_id"], "type": "DLGE", "payload": payload, "refs": entry["refs"],
            "sysmem": entry["sysmem"], "vidmem": entry["vidmem"]}


def resources(rows):
    """Merge all changed PL leaves sharing one DLGE instead of duplicating hashes."""
    database = open_index()
    output, edited = {}, {}
    try:
        for row in rows:
            if row["text_pl_display"] == row["text_pl_original"] and row.get("reviewed_subtitle_segments") is None:
                continue
            row["subtitle_patches"] = []
            uses = row.get("uses") or [{"line_id": row["line_id"], "leaf_index": row["leaf_index"], "archive": row["archive"]}]
            for use in uses:
                h = use["line_id"]
                if h not in output:
                    entries = database.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", (h,)).fetchall()
                    if len(entries) != 1 or entries[0]["archive"] != use["archive"]:
                        raise ValueError("Subtitle usage archive is ambiguous")
                    entry = entries[0]
                    with (ROOT / "Runtime" / entry["archive"]).open("rb") as stream:
                        original = read_resource(stream, entry)
                    output[h] = {"hash": h, "type": "DLGE", "payload": original, "refs": entry["refs"],
                                 "sysmem": entry["sysmem"], "vidmem": entry["vidmem"], "archive": entry["archive"],
                                 "original_payload_sha256": sha256(original)}
                item = output[h]
                parsed = parse(item["payload"], refs({"refs": item["refs"]}))
                leaf = parsed["leaves"][use["leaf_index"]]
                if row["audio_hash_en"] not in leaf["languages"]["en"]["audio_hashes"]:
                    raise ValueError("Subtitle leaf does not reference this audio")
                desired = display_parts(row)
                signature = (desired, row.get("reviewed_subtitle_segments"))
                key = (h, use["leaf_index"])
                if key in edited:
                    if edited[key] != signature:
                        raise ValueError("Conflicting edits to the same PL leaf")
                    continue
                text = leaf["languages"]["pl"]["text_raw"]
                old = text_fields(text)["segments"]
                if " ".join(segment["text"] for segment in old) != row["text_pl_original"]:
                    raise ValueError("Subtitle source text differs across audio uses")
                replacement = replacement_text(row, text, desired)
                item["payload"] = replace_polish(item["payload"], refs({"refs": item["refs"]}), use["leaf_index"], text, replacement)
                edited[key] = signature
                row["subtitle_patches"].append({"resource_hash": h, "leaf_index": use["leaf_index"],
                                                "original_payload_sha256": item["original_payload_sha256"], "only_language": "pl"})
    finally:
        database.close()
    return list(output.values())
