"""Reconcile billed credits with request IDs; library voices may cost more."""
import json

from .common import api_key
from .elevenlabs_api import get


def reconcile(store):
    pending = store.database.execute("SELECT id,document,account_id FROM entries WHERE status='succeeded' AND actual_credits IS NULL").fetchall()
    if not pending:
        return {"status": "history_verified", "pending": 0}
    matched = 0
    reasons = []
    for account in sorted({row[2] for row in pending}):
        lookup = {json.loads(document).get("request_id"): (identifier, json.loads(document))
                  for identifier, document, owner in pending if owner == account}
        key = api_key(account)
        if not key:
            reasons.append(f"Account {account} key missing; history not queried with another account")
            continue
        try:
            history = get("/v1/history?page_size=1000", key)
        except RuntimeError as error:
            reasons.append(f"Account {account}: {error}")
            continue
        with store.database:
            for item in history.get("history", []):
                match = lookup.get(item.get("request_id"))
                before, after = item.get("character_count_change_from"), item.get("character_count_change_to")
                if not match or not isinstance(before, int) or not isinstance(after, int) or after < before:
                    continue
                identifier, entry = match
                entry.update(actual_credits=after - before, credit_count_from=before, credit_count_to=after,
                             credit_cost_source="matched ElevenLabs history request ID")
                store.database.execute("UPDATE entries SET actual_credits=?,document=? WHERE id=?",
                                       (after - before, json.dumps(entry, ensure_ascii=False), identifier))
                matched += 1
    remaining = len(pending) - matched
    report = {"status": "history_verified" if not remaining else "conservative_credit_estimate", "pending": remaining}
    if reasons:
        report["reasons"] = reasons
    return report
