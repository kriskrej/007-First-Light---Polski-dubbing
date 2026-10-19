"""SQLite accounting for the explicitly authorized campaign."""
import json
import math
import sqlite3

from .common import ARTIFACTS, ROOT, sha256, active_account
from .tts import locked


class CampaignLedger:
    def __init__(self, path=None):
        # Validate local policy before opening a database, so missing setup cannot
        # leave a locked SQLite file behind on Windows.
        self.policy = json.loads((ROOT / "config" / "campaign.json").read_text(encoding="utf-8"))
        self.path = path or ARTIFACTS / "campaign" / "tts.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Fitting, one prefetched request and read-only progress may briefly
        # overlap a durable transaction. Wait for it instead of abandoning an
        # already-paid take after SQLite's five-second default.
        self.database = sqlite3.connect(self.path, timeout=30)
        self.database.execute("PRAGMA journal_mode=WAL")
        self.database.execute("PRAGMA synchronous=FULL")
        schema = {row[0] for row in self.database.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
        if 'entries' not in schema:
            self.database.execute("CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, audio_hash TEXT, cache_key TEXT, characters INTEGER, status TEXT, document TEXT)")
        if 'lookup_audio' not in schema:
            self.database.execute("CREATE INDEX IF NOT EXISTS lookup_audio ON entries(audio_hash)")
        if 'lookup_cache' not in schema:
            self.database.execute("CREATE INDEX IF NOT EXISTS lookup_cache ON entries(cache_key)")
        columns = {row[1] for row in self.database.execute("PRAGMA table_info(entries)")}
        if "account_id" not in columns:
            self.database.execute("ALTER TABLE entries ADD COLUMN account_id TEXT NOT NULL DEFAULT '1'")
        for name in ("reserved_credits", "actual_credits"):
            if name not in columns:
                self.database.execute(f"ALTER TABLE entries ADD COLUMN {name} INTEGER")
        with self.database:
            for identifier, document in self.database.execute("SELECT id,document FROM entries WHERE reserved_credits IS NULL").fetchall():
                entry = json.loads(document)
                credits = self.required_credits(entry["identity"])
                self.database.execute("UPDATE entries SET reserved_credits=?,actual_credits=? WHERE id=?",
                                      (credits, entry.get("actual_credits"), identifier))
        if self.database.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0:
            original = ARTIFACTS / "poc" / "tts-ledger.json"
            if original.exists():
                with self.database:
                    for entry in json.loads(original.read_text(encoding="utf-8"))["entries"]:
                        self._insert(entry)

    def locked(self):
        return locked(self.path)

    def load(self, audio_hash):
        entries = []
        for identifier, document in self.database.execute("SELECT id,document FROM entries WHERE audio_hash=? ORDER BY id", (audio_hash,)):
            entry = json.loads(document)
            entry["_db_id"] = identifier
            entries.append(entry)
        return {"entries": entries}

    def _insert(self, entry):
        entry.setdefault("reserved_credits", self.required_credits(entry["identity"]))
        cursor = self.database.execute("INSERT INTO entries(audio_hash,cache_key,characters,status,document,reserved_credits,actual_credits,account_id) VALUES(?,?,?,?,?,?,?,?)",
                                       (entry["audio_hash_en"], entry["cache_key"], entry["characters"], entry["status"], json.dumps(entry, ensure_ascii=False), entry["reserved_credits"], entry.get("actual_credits"), entry.get("account_id", "1")))
        entry["_db_id"] = cursor.lastrowid

    def required_credits(self, identity):
        self.policy = json.loads((ROOT / "config/campaign.json").read_text(encoding="utf-8"))
        verified = self.policy.get("voice_pricing_verified_ids")
        if verified is not None and identity.get("voice_id") is not None and identity["voice_id"] not in verified:
            raise RuntimeError("Voice pricing has not been verified; no paid request")
        multiplier = self.policy.get("voice_credit_multipliers", {}).get(identity.get("voice_id"), 1)
        if not isinstance(multiplier, (int, float)) or multiplier < 1:
            raise ValueError("Invalid conservative voice credit multiplier")
        return math.ceil(len(identity["request"]["text"]) * multiplier)

    def budget_credits(self):
        # Keep the reservation ceiling for the PLN cap even after a lower
        # plan-specific credit debit is known for a premium library voice.
        return self.database.execute("SELECT COALESCE(SUM(MAX(COALESCE(actual_credits,0),reserved_credits)),0) FROM entries").fetchone()[0]

    def quota_credits(self, account=None):
        clause, values = (" WHERE account_id=?", (account,)) if account else ("", ())
        return self.database.execute("SELECT COALESCE(SUM(CASE WHEN status='succeeded' THEN COALESCE(actual_credits,reserved_credits) ELSE reserved_credits END),0) FROM entries" + clause, values).fetchone()[0]

    def reviewed_casting_correction(self, identity):
        review = self.policy.get("reviewed_casting_corrections", {}).get(identity["audio_hash_en"])
        if not review:
            return False
        corrected_limit = review.get("corrected_versions_maximum", 1 if review.get("one_corrected_take_only") is True else 0)
        if corrected_limit not in (1, 2):
            return False
        prior = self.load(identity["audio_hash_en"])["entries"]
        if (not 3 <= len(prior) < 3 + corrected_limit or review.get("prior_versions") != 3
                or identity.get("voice_id") != review.get("correct_voice_id")
                or review.get("wrong_voice_id") == review.get("correct_voice_id")
                or any(e["status"] != "succeeded" or e["identity"].get("voice_id") != review.get("wrong_voice_id") for e in prior[:3])
                or any(e["status"] != "succeeded" or e["identity"].get("voice_id") != review.get("correct_voice_id") for e in prior[3:])):
            return False
        evidence = ROOT / review["evidence_path"]
        return evidence.is_file() and sha256(evidence.read_bytes()) == review.get("evidence_sha256")

    def reserve(self, identity):
        import time
        from datetime import datetime, timezone
        # Read the latest cap before every paid operation, including a running
        # batch whose user changes the budget while it is being processed.
        self.policy = json.loads((ROOT / "config/campaign.json").read_text(encoding="utf-8"))
        if self.policy.get("generation_hold"):
            raise RuntimeError("Paid generation held for diagnostics; cached results remain available")
        deadline = self.policy.get("night_run", {}).get("deadline")
        if deadline and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline):
            raise RuntimeError("Overnight work deadline reached; no further paid request")
        audio_hash = identity["audio_hash_en"]
        if self.database.execute("SELECT 1 FROM entries WHERE audio_hash=? AND status IN ('reserved','unknown')", (audio_hash,)).fetchone():
            raise RuntimeError("Unresolved campaign request; no paid retry")
        if (self.database.execute("SELECT COUNT(*) FROM entries WHERE audio_hash=?", (audio_hash,)).fetchone()[0] >= self.policy.get("max_versions_per_resource", 3)
                and not self.reviewed_casting_correction(identity)):
            raise ValueError("Campaign quality retake limit reached for this resource")
        characters = len(identity["request"]["text"])
        credits = self.required_credits(identity)
        total = self.budget_credits()
        price = self.policy["pricing_usd_per_1000"] * self.policy["usd_pln_mid"] * (1 + self.policy["tax_assumed"]) / 1000
        session = self.policy.get("authorized_credit_session")
        if session:
            baseline, allowance = session["ledger_baseline_credits"], session["max_additional_credits"]
            if not isinstance(baseline, int) or not isinstance(allowance, int) or baseline < 0 or allowance <= 0 or self.quota_credits() < baseline:
                raise ValueError("Invalid authorized credit session; no paid request")
            if self.quota_credits() - baseline + credits > allowance:
                raise RuntimeError("Authorized additional credit budget exhausted")
        elif (total + credits) * price > self.policy["working_value_limit_pln"]:
            raise RuntimeError("Campaign working budget exhausted")
        entry = {"audio_hash_en": audio_hash, "characters": characters, "reserved_credits": credits, "status": "reserved", "account_id": active_account(),
                 "started_unix": time.time(), "identity": identity,
                 "cache_key": sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode())}
        with self.database:
            self._insert(entry)
        return entry

    def write(self, ledger):
        with self.database:
            for entry in ledger["entries"]:
                self.database.execute("UPDATE entries SET status=?,document=?,actual_credits=? WHERE id=?",
                                      (entry["status"], json.dumps(entry, ensure_ascii=False), entry.get("actual_credits"), entry["_db_id"]))

    def usage(self):
        row = self.database.execute("SELECT COALESCE(SUM(characters),0), COUNT(*), COUNT(DISTINCT audio_hash), SUM(status='succeeded'), SUM(status IN ('unknown','reserved')), COALESCE(SUM(CASE WHEN status='succeeded' THEN characters ELSE 0 END),0) FROM entries").fetchone()
        price = self.policy["pricing_usd_per_1000"] * self.policy["usd_pln_mid"] * (1 + self.policy["tax_assumed"]) / 1000
        credits, exact, pending = self.database.execute("SELECT COALESCE(SUM(COALESCE(actual_credits,reserved_credits)),0),COALESCE(SUM(actual_credits),0),COALESCE(SUM(actual_credits IS NULL),0) FROM entries WHERE status='succeeded'").fetchone()
        accounts = {account: {"successful_generations": count, "accounted_credits": used}
                    for account, count, used in self.database.execute("SELECT account_id,COUNT(*),SUM(COALESCE(actual_credits,reserved_credits)) FROM entries WHERE status='succeeded' GROUP BY account_id")}
        report = {"submitted_characters": row[0], "requests": row[1], "distinct_lines": row[2],
                "successful_generations": row[3] or 0, "unresolved_requests": row[4] or 0,
                "successful_characters": row[5], "accounted_credits": credits,
                "history_verified_credits": exact, "generations_awaiting_credit_reconciliation": pending,
                "conservative_value_limit_accounted_pln": round(self.budget_credits() * price, 4),
                "estimated_usage_value_pln": round(credits * price, 4), "accounts": accounts}
        session = self.policy.get("authorized_credit_session")
        if session:
            used = self.quota_credits() - session["ledger_baseline_credits"]
            report["new_credit_session"] = {"accounted_credits": used,
                "maximum_credits": session["max_additional_credits"],
                "remaining_credits": max(0, session["max_additional_credits"] - used)}
        return report

    def close(self):
        self.database.close()
