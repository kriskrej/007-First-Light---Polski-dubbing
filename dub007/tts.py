"""Bounded, journaled ElevenLabs REST generation; never retries a paid POST."""
from contextlib import contextmanager
import json
import base64
import os
import re
from pathlib import Path
import time
import urllib.error
import urllib.parse
import urllib.request
import wave

from .common import ARTIFACTS, ROOT, api_key, save_json, sha256, active_account
from .elevenlabs_api import get
from .installer import durable_bytes

LEDGER = ARTIFACTS / "poc" / "tts-ledger.json"
LIMIT = 10000


@contextmanager
def locked(path=LEDGER):
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Another TTS generation is already running") from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def load(path=LEDGER):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"limit": LIMIT, "entries": []}


def reserve(ledger, identity):
    entries = ledger["entries"]
    line = identity["audio_hash_en"]
    characters = len(identity["request"]["text"])
    if any(item["audio_hash_en"] == line and item["status"] in ("reserved", "unknown") for item in entries):
        raise RuntimeError("Unresolved paid request for this audio; inspect history before any retry")
    if len({item["audio_hash_en"] for item in entries} | {line}) > 10:
        raise ValueError("PoC permits at most 10 distinct audio lines")
    if sum(item["audio_hash_en"] == line for item in entries) >= 2:
        raise ValueError("PoC permits at most one additional version per line")
    if sum(item["characters"] for item in entries) + characters > LIMIT:
        raise ValueError("PoC TTS character limit reached")
    entry = {"audio_hash_en": line, "characters": characters, "status": "reserved",
             "started_unix": time.time(), "identity": identity,
             "cache_key": sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode())}
    entries.append(entry)
    return entry


def pcm_to_wav(raw, output, rate):
    if not raw or len(raw) % 2:
        raise ValueError("Invalid 16-bit mono PCM response")
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(raw)


def recover(entry, key):
    query = urllib.parse.urlencode({"page_size": 100, "voice_id": entry["identity"]["voice_id"],
                                    "model_id": entry["identity"]["request"]["model_id"], "source": "TTS",
                                    "date_after_unix": int(entry["started_unix"]) - 5})
    history = get("/v1/history?" + query, key)
    matches = [item for item in history.get("history", [])
               if ((entry.get("request_id") and item.get("request_id") == entry["request_id"])
                   or (item.get("text") == entry["identity"]["request"]["text"]
                       and item.get("voice_id") == entry["identity"]["voice_id"]))
               and item.get("model_id") == entry["identity"]["request"]["model_id"]
               and abs(item.get("date_unix", 0) - entry["started_unix"]) < 180
               and all(item.get("settings", {}).get(name) == value
                       for name, value in entry["identity"]["request"]["voice_settings"].items())]
    entry["history_checked_unix"] = time.time()
    entry["history_matches"] = [{name: item.get(name) for name in
                                 ("history_item_id", "request_id", "content_type", "date_unix",
                                  "character_count_change_from", "character_count_change_to")} for item in matches]
    return matches


def recover_audio(entry, match, key, generated_root=None):
    """Retrieve an identified result using GET, with no new paid generation."""
    folder = (generated_root or ARTIFACTS / "poc" / "generated") / entry["cache_key"]
    folder.mkdir(parents=True, exist_ok=True)
    identifier = urllib.parse.quote(match["history_item_id"], safe="")
    request = urllib.request.Request(f"https://api.elevenlabs.io/v1/history/{identifier}/audio",
                                     headers={"xi-api-key": key})
    with urllib.request.urlopen(request, timeout=60) as response:
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        raw = response.read()
    durable_bytes(folder / "history-response.bin", raw)
    output = folder / "raw.wav"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        durable_bytes(output, raw)
    elif content_type == "audio/pcm":
        pcm_to_wav(raw, output, 24000)
    elif content_type in ("audio/mpeg", "audio/mp3"):
        import subprocess
        from .audio import ffmpeg
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i",
                        str(folder / "history-response.bin"), "-ar", "24000", "-ac", "1",
                        "-c:a", "pcm_s16le", str(output)], capture_output=True, check=True)
    else:
        raise RuntimeError("Unrecognized history audio format; no paid retry")
    with wave.open(str(output), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getnframes() == 0:
            raise ValueError("Recovered history audio failed WAV validation")
    entry.update(status="succeeded", completed_unix=time.time(), history_item_id=match["history_item_id"],
                 request_id=match["request_id"], recovered_via_get=True, history_audio_format=content_type,
                 audio_path=str(output.relative_to(ROOT)), audio_sha256=sha256(output.read_bytes()))


def validate_casting(speaker_id, speaker, voices):
    voice = next(item for item in voices if item['voice_id'] == speaker['voice_id'])
    native = re.search(r'(?:CIV|HNC|MERC)[A-Z]{2,3}([MF])\d{2}$', speaker_id)
    expected = speaker.get('expected_gender') or ({'M': 'male', 'F': 'female'}[native[1]] if native else None)
    observed = voice.get('labels', {}).get('gender')
    if expected and observed in ('male', 'female') and expected != observed:
        raise ValueError('Casting gender conflicts with reviewed role/native actor code; no paid request')


FOREIGN_LANGUAGE_TAG = re.compile(
    r"\[(?:speaking\s+in|in|speaks)\s+"
    r"(?:Serbian|Hindi|Cantonese|French|Italian|German|Portuguese|Vietnamese|Romanian|"
    r"Russian|Japanese|Mandarin|Chinese|Arabic|Polish|Spanish|Bulgarian|Slovak|Czech|Korean|Turkish)\b",
    re.IGNORECASE)


def validate_language(row):
    """Hold explicit foreign speech before paid generation, except reviewed mixed lines."""
    if not any(FOREIGN_LANGUAGE_TAG.search(row.get(key, "") or "")
               for key in ("text_en", "text_source_script")):
        return
    count = len(row.get("segments_pl", []))
    preserved = set(row.get("preserve_original_segment_indices", []))
    if preserved and len(preserved) < count and all(
            isinstance(index, int) and 0 <= index < count for index in preserved):
        return
    raise ValueError("Explicit foreign-language speech needs original-audio review or reviewed mixed-segment preservation before any paid request")


def generate(row, *, store=None, casting_path=None, with_timestamps=False):
    validate_language(row)
    from .pronunciation import apply
    apply(row)
    casting = json.loads((casting_path or ROOT / "config" / "casting.json").read_text(encoding="utf-8"))
    speaker = casting["speakers"][row["speaker_id"]]
    row.update(voice_id=speaker["voice_id"], model_id=casting["model_id"], settings=casting["settings"])
    key = api_key()
    if not key:
        raise RuntimeError("ElevenLabs key missing")
    preflight = json.loads((ARTIFACTS / "elevenlabs_preflight.json").read_text(encoding="utf-8"))
    if preflight.get("account_id", "1") != active_account():
        raise ValueError("Run read-only api-check for the selected account before generating")
    if active_account() == "2" and (not preflight.get("distinct_account_verified")
            or preflight.get("credential_fingerprint") != sha256(key.encode())):
        raise ValueError("Second account identity and credentials must pass read-only api-check before generating")
    model = next((item for item in preflight["models"] if item["model_id"] == row["model_id"]), None)
    if not model or not any(language["language_id"] == "pl" for language in model["languages"]):
        raise ValueError("Chosen model is unavailable or lacks Polish")
    if row["voice_id"] not in {voice["voice_id"] for voice in preflight["voices"]}:
        raise ValueError("Chosen voice is unavailable")
    validate_casting(row['speaker_id'], speaker, preflight['voices'])
    if set(row["settings"]) != {"stability"}:
        raise ValueError("Only the verified stability setting is currently supported")
    # Creator supports 24-kHz lossless PCM; 44.1-kHz PCM requires Pro per API docs.
    request_body = {"text": row["text_pl_tts"], "model_id": row["model_id"],
                    "voice_settings": row["settings"]}
    if row["model_id"] != "eleven_multilingual_v2":
        request_body["language_code"] = "pl"
    identity = {"audio_hash_en": row["audio_hash_en"], "voice_id": row["voice_id"],
                "output_format": "pcm_24000", "request": request_body,
                "direction": casting["speakers"][row["speaker_id"]]["direction"],
                "pronunciation": row["text_pl_tts"], "time_profile": {
                    "original_duration": row["duration"], "sample_count": row["sample_count"],
                    "sample_rate": row["sample_rate"], "markers": row["original_markers"]}}
    if with_timestamps:
        identity["with_timestamps"] = True
    cache_key = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode())
    generated_root = ARTIFACTS / ("campaign" if store else "poc") / "generated"
    with (store.locked() if store else locked()):
        ledger = store.load(row["audio_hash_en"]) if store else load()
        def persist():
            store.write(ledger) if store else save_json(LEDGER, ledger)
        cached = next((item for item in ledger["entries"] if item["cache_key"] == cache_key and item["status"] == "succeeded"), None)
        if cached:
            audio = ROOT / cached["audio_path"]
            if not audio.is_file() or sha256(audio.read_bytes()) != cached["audio_sha256"]:
                raise ValueError("Cached audio missing or checksum mismatch; do not regenerate blindly")
            return cached
        pending = next((item for item in ledger["entries"] if item["audio_hash_en"] == row["audio_hash_en"]
                        and item["status"] in ("reserved", "unknown")), None)
        if pending:
            recovery_key = api_key(pending.get("account_id", "1"))
            if not recovery_key:
                raise RuntimeError("Original account key required to recover pending request; no paid retry")
            matches = recover(pending, recovery_key)
            if len(matches) == 1:
                recover_audio(pending, matches[0], recovery_key, generated_root)
                persist()
                return pending
            persist()
            raise RuntimeError("Unresolved request: history inspected and saved to tts-ledger.json; paid POST not repeated")
        from .elevenlabs_api import included_subscription
        subscription = included_subscription(key, store)
        credits_needed = store.required_credits(identity) if store else len(request_body["text"])
        if subscription.get("character_limit", 0) - subscription.get("character_count", 0) < credits_needed:
            raise RuntimeError("Insufficient included credits; no overage or plan change will be requested")
        entry = store.reserve(identity) if store else reserve(ledger, identity)
        if store:
            ledger["entries"].append(entry)
        entry["account_count_before"] = subscription.get("character_count")
        entry["account_count_is_projection"] = subscription.get("count_is_conservative_projection", False)
        folder = generated_root / cache_key
        folder.mkdir(parents=True, exist_ok=True)
        persist()  # Reservation is durable BEFORE the paid POST.
        suffix = "/with-timestamps" if with_timestamps else ""
        request = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{row['voice_id']}{suffix}?output_format=pcm_24000",
            data=json.dumps(request_body, ensure_ascii=False).encode(), method="POST",
            headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "application/json" if with_timestamps else "audio/pcm"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                entry["request_id"] = response.headers.get("request-id") or response.headers.get("x-request-id")
                entry["content_type"] = response.headers.get("content-type")
                entry["character_cost_header"] = response.headers.get("character-cost")
                persist()
                raw = response.read()
            if with_timestamps:
                durable_bytes(folder / "response.json", raw)
                response_document = json.loads(raw)
                raw = base64.b64decode(response_document["audio_base64"], validate=True)
                save_json(folder / "alignment.json", {name: response_document.get(name) for name in ("alignment", "normalized_alignment")})
                entry["alignment_path"] = str((folder / "alignment.json").relative_to(ROOT))
            durable_bytes(folder / "response.pcm", raw)
            pcm_to_wav(raw, folder / "raw.wav", 24000)
            entry.update(status="succeeded", completed_unix=time.time(), output_sample_rate=24000,
                         audio_path=str((folder / "raw.wav").relative_to(ROOT)),
                         audio_sha256=sha256((folder / "raw.wav").read_bytes()))
            persist()
            return entry
        except urllib.error.HTTPError as error:
            # Conservative character accounting includes rejected submissions.
            detail = error.read(8192).decode("utf-8", errors="replace").replace(key, "[redacted]")
            entry.update(status="http_rejected" if 400 <= error.code < 500 else "unknown",
                         http_status=error.code, error_detail=detail)
            persist()
            raise RuntimeError(f"ElevenLabs TTS HTTP {error.code}; request saved, no automatic retry. {detail[:600]}") from None
        except Exception:
            entry["status"] = "unknown"
            persist()
            try:
                recover(entry, key)
                persist()
            except RuntimeError:
                pass
            raise RuntimeError("TTS result uncertain; history checked where accessible, no paid retry") from None


def usage():
    entries = load()["entries"]
    return {"submitted_characters": sum(item["characters"] for item in entries),
            "successful_generations": sum(item["status"] == "succeeded" for item in entries),
            "unknown_requests": sum(item["status"] in ("unknown", "reserved") for item in entries),
            "distinct_lines": len({item["audio_hash_en"] for item in entries}), "limit": LIMIT}
