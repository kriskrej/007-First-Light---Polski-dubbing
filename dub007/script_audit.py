"""Recover source-script dialogue when localized captions are accessibility labels."""
import json
import re
from collections import Counter

from .archive import open_index, refs, read_resource
from .common import ROOT, save_json
from .dlge import parse
from .campaign import database, save_row


def run():
    store, index = database(), open_index()
    cache, streams, counts = {}, {}, Counter()
    try:
        for (document,) in store.database.execute("SELECT document FROM resources ORDER BY position").fetchall():
            row = json.loads(document)
            h = row["line_id"]
            if h not in cache:
                entry = index.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", (h,)).fetchone()
                if entry["archive"] not in streams:
                    streams[entry["archive"]] = (ROOT / "Runtime" / entry["archive"]).open("rb")
                cache[h] = parse(read_resource(streams[entry["archive"]], entry), refs(entry))
            source = cache[h]["leaves"][row["leaf_index"]]["languages"]["source"]
            row["text_source_script"] = " ".join(segment["text"] for segment in source["segments"])
            row["source_script_metadata"] = source["fields_raw"]
            row["source_script_segments"] = source["segments"]
            caption_label = re.fullmatch(r"\s*\[([^]]+)\]\s*", row["text_en"])
            if caption_label:
                label = caption_label[1].casefold()
                if "communicating" in label or "foreign" in label:
                    row["speech_classification"] = "intentional_foreign_speech"
                elif any(word in label for word in ("grunt", "sigh", "breath", "laugh", "scream", "gasp", "cough", "effort", "groan", "cry", "whistle")):
                    row["speech_classification"] = "nonverbal"
                else:
                    row["speech_classification"] = "caption_label_needs_script_review"
            else:
                row["speech_classification"] = "localized_verbal_dialogue"
            counts[row["speech_classification"]] += 1
            save_row(store, row)
        report = {"mission": "clover", "counts": dict(counts),
                  "finding": "EN/PL [Hostiles communicating] is an accessibility caption, source block 0 contains the dialogue script and Serbian/Balkan instructions"}
        save_json(ROOT / "docs/SCRIPT_AUDIT.json", report)
        return report
    finally:
        for stream in streams.values():
            stream.close()
        store.close()
        index.close()
