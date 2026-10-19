"""Report text, generated audio and installed audio as separate measurements."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .common import ARTIFACTS, ROOT, save_json


READY = "offline_final_wem_validated"
PRESERVED = {"preserved_foreign_language", "preserved_nonverbal"}


def run():
    from .campaign import chapter_config, database
    from .deployment import active_install

    configuration = chapter_config()
    store = database()
    try:
        # One snapshot keeps chapter and global counters internally consistent
        # while the separate generation process commits new resources.
        store.database.execute("BEGIN")
        documents = {h: json.loads(d) for h, d in store.database.execute("SELECT audio_hash,document FROM resources")}
        memberships = list(store.database.execute("SELECT chapter,audio_hash FROM chapter_resources"))
        usage = store.usage()
        store.database.rollback()
    finally:
        store.close()
    ready = {h for h, row in documents.items() if row["qa_status"] == READY}
    preserved = {h for h, row in documents.items() if row["qa_status"] in PRESERVED}
    installed = active_install()
    installed_hashes = set()
    if installed:
        bundle = json.loads(Path(installed["bundle"]).read_text(encoding="utf-8"))
        installed_hashes = {r["hash"] for r in bundle["resources"] if r.get("type", "WWES") == "WWES"}
    chapters = []
    for chapter, details in configuration["chapters"].items():
        hashes = {h for c, h in memberships if c == chapter}
        chapters.append({"chapter": chapter, "number": details.get("number"), "title": details["title"],
                         "scope_kind": details.get("scope_kind", "mapped_story_scope"),
                         "mapped_total": len(hashes), "ready_polish": len(hashes & ready),
                         "installed_polish": len(hashes & installed_hashes),
                         "preserved_original": len(hashes & preserved),
                         "processed_percent": round(len(hashes & (ready | preserved)) / max(len(hashes), 1) * 100, 3),
                         "mapping_status": "prepared" if hashes else "not_prepared_yet",
                         "awaiting_adaptation": sum(documents[h]["qa_status"] == "needs_agent_adaptation" for h in hashes),
                         "paused": details.get("paused", False), "runtime_verified": False})
    audio_paths = {r["hash"]: r["path"] for r in json.loads(
        (ROOT / "research/bond-hashes/paths/WWES.json").read_text(encoding="utf-8"))}
    corpus, unresolved, source_only = {}, set(), set()
    with (ARTIFACTS / "texts/dialogue-mapped.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            h = row.get("audio_hash_en")
            if row.get("status") == "parse_failed":
                unresolved.add(row["line_id"])
            if not h:
                continue
            if row.get("text_en") or row.get("text_pl_original"):
                previous = corpus.setdefault(h, {"official_pl": False})
                previous["official_pl"] |= bool(row.get("text_pl_original"))
            elif row.get("text_source_script"):
                source_only.add(h)
    groups = Counter()
    for h in corpus.keys() - ready - preserved:
        match = re.search(r"/voices/english\(us\)/([^/]+)/", audio_paths.get(h, ""))
        groups[match[1] if match else "unknown_audio_path"] += 1
    total = len(corpus)
    official_pl = sum(row["official_pl"] for row in corpus.values())
    shared = {}
    for namespace in ("ai_dialog", "globalvo"):
        hashes = {h for h in corpus if f"/voices/english(us)/{namespace}/" in audio_paths.get(h, "")}
        shared[namespace] = {"known_captioned_audio": len(hashes), "ready_polish": len(hashes & ready),
                             "installed_polish": len(hashes & installed_hashes),
                             "preserved_original": len(hashes & preserved),
                             "polish_percent": round(len(hashes & ready) / max(len(hashes), 1) * 100, 3)}
    for scope, details in configuration["chapters"].items():
        if details.get("scope_kind") in ("native_library", "reactive"):
            hashes = {h for c, h in memberships if c == scope} & corpus.keys()
            shared[scope] = {"known_captioned_audio": len(hashes),
                             "ready_polish": len(hashes & ready),
                             "installed_polish": len(hashes & installed_hashes),
                             "preserved_original": len(hashes & preserved),
                             "polish_percent": round(len(hashes & ready) / max(len(hashes), 1) * 100, 3),
                             "scope_note": "Verified shared library; no numbered chapter assignment"}
    runtime = json.loads((ROOT / "docs/RUNTIME_VALIDATION.json").read_text(encoding="utf-8"))
    chapters.sort(key=lambda chapter: chapter["number"] if chapter["number"] is not None else 999)
    report = {"checked_utc": datetime.now(timezone.utc).isoformat(),
              "measurement": "Unique audio hashes with EN/PL captions; not gameplay duration or all audible speech",
              "known_captioned_audio": total, "ready_polish_audio": len(ready),
              "ready_captioned_polish_audio": len(ready & corpus.keys()),
              "ready_additional_audio_without_caption": len(ready - corpus.keys()),
              "ready_polish_percent": round(len(ready & corpus.keys()) / max(total, 1) * 100, 3),
              "installed_polish_audio": len(installed_hashes),
              "installed_captioned_polish_audio": len(installed_hashes & corpus.keys()),
              "installed_polish_percent": round(len(installed_hashes & corpus.keys()) / max(total, 1) * 100, 3),
              "preserved_original_audio": len(preserved),
              "preserved_captioned_original_audio": len(preserved & corpus.keys()),
              "preserved_additional_audio_without_caption": len(preserved - corpus.keys()),
              "remaining_captioned_audio": len(corpus.keys() - ready - preserved),
              "preserved_original_note": "Campaign resources retained in their original form; separate from Polish-ready audio. Additional captionless census decisions are reported separately.",
              "official_polish_text_available": official_pl,
              "official_polish_text_available_percent": round(official_pl / max(total, 1) * 100, 3),
              "official_polish_text_note": "Existing game translations recovered locally; this is not agent-authored translation progress or UI coverage",
              "remaining_captioned_audio_by_namespace": dict(groups.most_common()),
              "unresolved_dlge": len(unresolved), "additional_source_only_audio": len(source_only - corpus.keys()),
              "source_only_note": "Needs classification; source script can contain placeholders, not necessarily spoken text",
              "chapters": chapters, "shared_libraries": shared, "usage": usage,
              "active_install_id": installed["install_id"] if installed else None,
              "runtime_confirmed_audio": len(runtime.get("verified_audio_hashes", [])),
              "runtime_note": runtime["limitations"]}
    save_json(ROOT / "docs/PROGRESS_AUDIT.json", report)
    return report
