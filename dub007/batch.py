"""First passes queue timing reviews without automatically purchasing retakes."""
import json

from .campaign import database, ensure_chapter, progress, run as generate


def run(chapter):
    deferred = []
    while True:
        store = database()
        try:
            ensure_chapter(store, chapter)
            rows = [json.loads(d) for d, in store.database.execute(
                "SELECT r.document FROM chapter_resources c JOIN resources r ON c.audio_hash=r.audio_hash "
                "WHERE c.chapter=? ORDER BY c.ordinal", (chapter,))]
            fresh = [r["audio_hash_en"] for r in rows if r.get("generation_status") == "not_started"
                     and r["qa_status"] == "original_metadata_verified"]
            if not fresh:
                report = progress(store, chapter)
                report.update(first_pass_complete=True,
                              remaining_script_review=sum(r["qa_status"] == "needs_agent_script_review" for r in rows),
                              remaining_adaptations=sum(r["qa_status"] == "needs_agent_adaptation" for r in rows),
                              automatically_purchased_retakes=0)
                return report
        finally:
            store.close()
        report = generate(len(fresh), chapter=chapter, audio_hashes=fresh)
        if report.get("needs_attention"):
            h = report["needs_attention"]["audio_hash_en"]
            if h in deferred:
                raise RuntimeError("First pass made no progress; no automatic paid retry")
            deferred.append(h)
            print(json.dumps({"queued_for_agent_adaptation": h,
                              "reason": report["needs_attention"]["qa_error"]}), flush=True)
        elif report.get("needs_script_review"):
            raise RuntimeError("First-pass script state changed; review before continuing")
