"""ElevenLabs read-only REST preflight. No paid POST requests in this module."""
import json
import time
import urllib.error
import urllib.request

from .common import ARTIFACTS, api_key, save_json, active_account, sha256


def get(endpoint, key):
    request = urllib.request.Request("https://api.elevenlabs.io" + endpoint,
                                     headers={"xi-api-key": key, "Accept": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < 5:
                retry = error.headers.get("Retry-After", "") if error.headers else ""
                delay = float(retry) if retry.replace(".", "", 1).isdigit() else min(60, 10 * 2 ** attempt)
                # These are read-only GET retries, never a paid POST retry.
                remaining = max(1, delay)
                while remaining:
                    interval = min(60, remaining)
                    time.sleep(interval)
                    remaining -= interval
                continue
            raise RuntimeError(f"ElevenLabs GET {endpoint}: HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError(f"ElevenLabs GET {endpoint}: connection failed") from None


def included_subscription(key, store=None):
    """Read the allowance at most once per minute and account for local work."""
    if store is None:
        return get("/v1/user/subscription", key)
    account = active_account()
    path = ARTIFACTS / "campaign" / f"account-{account}-credit-snapshot.json"
    submitted = store.quota_credits(account)
    epoch = store.policy.get("account_credit_epochs", {}).get(account, {}).get("ledger_baseline_credits", 0)
    snapshot = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    fingerprint = sha256(key.encode())
    if not snapshot or snapshot.get("accounting_version") != 3 or snapshot.get("ledger_quota_epoch", 0) != epoch or snapshot.get("credential_fingerprint") != fingerprint or time.time() - snapshot["checked_unix"] >= 60:
        subscription = get("/v1/user/subscription", key)
        fields = ("tier", "status", "character_count", "character_limit", "can_extend_character_limit")
        snapshot = {"checked_unix": time.time(), "ledger_submitted": submitted, "accounting_version": 3,
                    "account_id": account, "credential_fingerprint": fingerprint,
                    "ledger_quota_epoch": epoch,
                    "account": {field: subscription.get(field) for field in fields}}
        save_json(path, snapshot)
    projected = dict(snapshot["account"])
    projected["character_count"] = max(max(0, submitted - epoch), (projected.get("character_count") or 0) + max(0, submitted - snapshot["ledger_submitted"]))
    projected["count_is_conservative_projection"] = True
    return projected


def verify_second_account(key):
    """Two API keys can share a workspace and therefore the same allowance."""
    first_key = api_key("1")
    if not first_key:
        raise RuntimeError("First account key required to verify that the second account has a separate allowance")
    first = get("/v1/user", first_key)
    second = get("/v1/user", key)
    if not first.get("user_id") or not second.get("user_id"):
        raise RuntimeError("Account identity unavailable; cannot verify separate allowances")
    if first["user_id"] == second["user_id"]:
        raise RuntimeError("Both API keys belong to the same ElevenLabs account and share its remaining credits; supply a key from the other account")


def check():
    key = api_key()
    if not key:
        return {"status": "missing_key", "tts_characters_sent": 0,
                "instruction": "Set ELEVENLABS_API_KEY in the local ignored .env file"}
    if active_account() == "2":
        verify_second_account(key)
    models = get("/v1/models", key)
    subscription = get("/v1/user/subscription", key)
    voices = get("/v2/voices?page_size=100", key)
    selected_models = [{field: model.get(field) for field in
                       ("model_id", "can_do_text_to_speech", "can_use_style", "can_use_speaker_boost",
                        "requires_alpha_access", "maximum_text_length_per_request", "model_rates", "languages")}
                       for model in models if model.get("can_do_text_to_speech")]
    account = {field: subscription.get(field) for field in
               ("tier", "status", "character_count", "character_limit", "can_extend_character_limit",
                "currency", "next_invoice", "has_open_invoices") if field in subscription}
    from .tts import usage
    report = {"status": "read_only_preflight_complete", "account_id": active_account(), "tts_characters_sent": usage()["submitted_characters"],
              "credential_fingerprint": sha256(key.encode()),
              "distinct_account_verified": active_account() == "2",
              "models": selected_models, "account": account,
              "voices": [{"voice_id": voice["voice_id"], "name": voice.get("name"),
                          "category": voice.get("category"), "labels": voice.get("labels"),
                          "verified_languages": voice.get("verified_languages")}
                         for voice in voices.get("voices", [])],
              "voices_has_more": voices.get("has_more", False)}
    save_json(ARTIFACTS / "elevenlabs_preflight.json", report)
    return report
