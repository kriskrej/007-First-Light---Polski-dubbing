"""Chronological, resumable campaign dubbing after the confirmed game test."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import wave

from .audio import prepare_campaign as fit_audio, encode, read_wav
from .common import ARTIFACTS, ROOT, save_json, sha256
from .ledger import CampaignLedger
from .tts import generate
from .timing import prepare_campaign_segments as prepare_segments

CASTING = ROOT / "config" / "first-mission-casting.json"


def chapter_config():
    path = ROOT / "config/chapters.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"active_chapter": "clover", "chapters": {}}


def scene_key(row):
    if row.get("native_library_scope"):
        from .native import library_scene_key
        configuration = chapter_config()["chapters"].get(row["native_library_scope"], {})
        return library_scene_key(row, configuration)
    if row.get("reactive_scope"):
        from .reactive import scene_key as reactive_scene_key
        configuration = chapter_config()["chapters"].get(row["reactive_scope"], {})
        if configuration.get("scope_kind") != "reactive":
            raise ValueError("Unknown reviewed reactive scope")
        return reactive_scene_key(row, configuration)
    path = row.get("audio_source_path") or row.get("dialogue_source_path") or ""
    if row.get("native_audio_label"):
        path = "/" + row["native_audio_label"] + ".wav"
    match = re.search(r"/([as](\d+)[^/?.]*?)_" + re.escape(row["speaker_id"].lower()) + r"_(\d{3})(?:_alt\d+)?(?:_(?:combat|regular|stealth)_?)?\.wav", path)
    if match:
        return int(match[2]), match[1], int(match[3]), row["audio_hash_en"]
    match = re.search(r"/([as](\d+)[^/?.]*)\.sweetdialog", path)
    if match:
        return int(match[2]), match[1], 999, row["audio_hash_en"]
    # Some reactive setpieces have native names without a scene number.
    # Accept only an explicitly reviewed phase anchor; never sort unknowns last.
    source = row.get("audio_source_path") or ""
    anchors = [anchor for chapter in chapter_config()["chapters"].values()
               for anchor in chapter.get("unnumbered_scene_anchors", [])
               if source.startswith(anchor["path_prefix"])]
    if len(anchors) == 1 and anchors[0].get("reason"):
        anchor = anchors[0]
        name = source.rsplit("/", 1)[-1].split(".wav", 1)[0]
        ending = re.search(r"_(\d{3})$", name)
        if ending and isinstance(anchor["after"], int) and anchor["after"] > 0:
            return anchor["after"], name[:ending.start()], int(ending[1]), row["audio_hash_en"]
    raise ValueError(f"Unknown chronological scene for {row['audio_hash_en']}")


def database():
    store = CampaignLedger()
    store.database.execute("CREATE TABLE IF NOT EXISTS resources (audio_hash TEXT PRIMARY KEY, position INTEGER UNIQUE, mission TEXT, scene TEXT, status TEXT, document TEXT)")
    store.database.execute("CREATE TABLE IF NOT EXISTS state (name TEXT PRIMARY KEY, value TEXT)")
    store.database.execute("CREATE TABLE IF NOT EXISTS chapter_resources (chapter TEXT, audio_hash TEXT, ordinal INTEGER, PRIMARY KEY(chapter,audio_hash))")
    with store.database:
        store.database.execute("INSERT OR IGNORE INTO chapter_resources SELECT mission,audio_hash,position FROM resources")
    return store


def order_key(row, rules=()):
    """Keep native scene IDs while placing reviewed branches at their story anchor."""
    key = scene_key(row)
    for rule in rules:
        if key[1].startswith(rule["prefix"]) and rule["first"] <= key[0] <= rule["last"]:
            return (rule["after"], 1, *key)
    return (key[0], 0, *key)


def ensure_ordering(store, chapter, *, force=False):
    rules = chapter_config()["chapters"][chapter].get("scene_ordering_rules", [])
    digest = sha256(json.dumps(rules, sort_keys=True).encode())
    state_key = f"chapter_ordering:{chapter}"
    previous = store.database.execute("SELECT value FROM state WHERE name=?", (state_key,)).fetchone()
    if previous and previous[0] == digest and not force:
        return
    documents = store.database.execute("SELECT r.document FROM chapter_resources c JOIN resources r ON r.audio_hash=c.audio_hash WHERE c.chapter=?", (chapter,)).fetchall()
    rows = sorted((json.loads(document) for (document,) in documents), key=lambda row: order_key(row, rules))
    with store.database:
        store.database.executemany("UPDATE chapter_resources SET ordinal=? WHERE chapter=? AND audio_hash=?",
                                   ((ordinal, chapter, row["audio_hash_en"]) for ordinal, row in enumerate(rows)))
        store.database.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (state_key, digest))


def ensure_supplements(store, chapter):
    changed = False
    for name in chapter_config()["chapters"][chapter].get("shared_prepared_manifests", []):
        path = ARTIFACTS / "chapters" / name / "manifest.json"
        digest = sha256(path.read_bytes())
        state_key = f"chapter_manifest:{chapter}:{name}"
        previous = store.database.execute("SELECT value FROM state WHERE name=?", (state_key,)).fetchone()
        if previous and previous[0] == digest:
            continue
        ensure_chapter(store, chapter, prepared_path=path)
        with store.database:
            store.database.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (state_key, digest))
        changed = True
    ensure_ordering(store, chapter, force=changed)


def ensure_chapter(store, chapter, prepared_path=None):
    if prepared_path is None and store.database.execute("SELECT 1 FROM chapter_resources WHERE chapter=?", (chapter,)).fetchone():
        ensure_supplements(store, chapter)
        return
    configuration = chapter_config()["chapters"].get(chapter)
    if not configuration:
        raise ValueError(f"Unmapped chapter: {chapter}")
    manifest = prepared_path or ARTIFACTS / "chapters" / chapter / "manifest.json"
    if not manifest.exists():
        from .mission import run as prepare_mission
        prepare_mission(configuration["mission_folder"], chapter,
                        audio_path_prefixes=configuration.get("source_audio_prefixes"),
                        dialogue_path_prefixes=configuration.get("source_dialogue_prefixes"))
    prepared = json.loads(manifest.read_text(encoding="utf-8"))
    if prepared["unresolved"]:
        raise ValueError("Chapter has unresolved DLGE mappings")
    rows = sorted(prepared["rows"], key=lambda row: order_key(row, configuration.get("scene_ordering_rules", [])))
    position = store.database.execute("SELECT COALESCE(MAX(position),-1)+1 FROM resources").fetchone()[0]
    ordinal_start = store.database.execute("SELECT COALESCE(MAX(ordinal),-1)+1 FROM chapter_resources WHERE chapter=?", (chapter,)).fetchone()[0]
    with store.database:
        for ordinal, row in enumerate(rows, ordinal_start):
            audio_hash = row["audio_hash_en"]
            existing = store.database.execute("SELECT document FROM resources WHERE audio_hash=?", (audio_hash,)).fetchone()
            if existing:
                before = json.loads(existing[0])
                if any(before[field] != row[field] for field in ("speaker_id", "text_pl_original", "original_payload_sha256")):
                    raise ValueError("Shared chapter audio has a conflicting use")
                merged = {(use["line_id"], use["leaf_index"]): use for use in before["uses"] + row["uses"]}
                before["uses"] = list(merged.values())
                store.database.execute("UPDATE resources SET document=? WHERE audio_hash=?", (json.dumps(before, ensure_ascii=False), audio_hash))
            else:
                key = scene_key(row)
                row.update(scene_id=key[1], scene_line_number=key[2], scene_number=key[0],
                           chapter_id=chapter, mission_id=prepared.get("mission_id", configuration["mission_folder"]))
                if configuration.get("scope_kind") in ("reactive", "native_library"):
                    row["scene_number"] = None
                text = row["text_pl_original"]
                if text.isupper():
                    text = text.capitalize()
                row["text_pl_tts"] = "[" + row.get("tts_audio_tag", "snappy") + "] " + text
                caption = re.fullmatch(r"\s*\[[^]]+\]\s*", row["text_en"])
                if caption or not text:
                    row["qa_status"] = "needs_agent_script_review"
                store.database.execute("INSERT INTO resources VALUES(?,?,?,?,?,?)",
                                       (audio_hash, position, chapter, row["scene_id"], row["qa_status"], json.dumps(row, ensure_ascii=False)))
                position += 1
            store.database.execute("INSERT OR IGNORE INTO chapter_resources VALUES(?,?,?)", (chapter, audio_hash, ordinal))
    if prepared_path is None:
        ensure_supplements(store, chapter)


def save_row(store, row):
    with store.database:
        store.database.execute("UPDATE resources SET status=?,document=? WHERE audio_hash=?",
                               (row["qa_status"], json.dumps(row, ensure_ascii=False), row["audio_hash_en"]))


def initialize():
    proof = json.loads((ROOT / "docs/RUNTIME_VALIDATION.json").read_text(encoding="utf-8"))
    if not proof.get("verified_audio_hashes"):
        raise RuntimeError("Game playback validation is required before campaign generation")
    store = database()
    if store.database.execute("SELECT COUNT(*) FROM resources").fetchone()[0]:
        return store
    mission = json.loads((ARTIFACTS / "first-mission/manifest.json").read_text(encoding="utf-8"))
    poc = {row["audio_hash_en"]: row for row in json.loads((ARTIFACTS / "poc/manifest.json").read_text(encoding="utf-8"))["rows"]}
    rows = sorted(mission["rows"], key=scene_key)
    with store.database:
        for position, row in enumerate(rows):
            key = scene_key(row)
            row.update(scene_id=key[1], scene_line_number=key[2], scene_number=key[0], mission_id="clover")
            if row["audio_hash_en"] in poc:
                existing = poc[row["audio_hash_en"]]
                if existing["original_payload_sha256"] != row["original_payload_sha256"]:
                    raise ValueError("PoC source differs from campaign source")
                row.update(existing)
                row.update(scene_id=key[1], scene_line_number=key[2], scene_number=key[0], mission_id="clover")
            else:
                text = row["text_pl_original"]
                if text.isupper():
                    text = text.capitalize()
                row["text_pl_tts"] = "[snappy] " + text
            store.database.execute("INSERT INTO resources VALUES(?,?,?,?,?,?)",
                                   (row["audio_hash_en"], position, "clover", row["scene_id"], row["qa_status"], json.dumps(row, ensure_ascii=False)))
            store.database.execute("INSERT OR IGNORE INTO chapter_resources VALUES(?,?,?)", ("clover", row["audio_hash_en"], position))
    return store


def original_wav(row):
    wav = ARTIFACTS / "campaign" / "originals" / f"{row['audio_hash_en']}.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    expected = (row['sample_rate'], row['channels'], row['sample_count'], 2)
    def profile(path):
        with wave.open(str(path), 'rb') as stream:
            return stream.getframerate(), stream.getnchannels(), stream.getnframes(), stream.getsampwidth()
    try:
        cache_valid = profile(wav) == expected
    except (OSError, EOFError, wave.Error):
        cache_valid = False
    if not cache_valid:
        vgm = ROOT / "research/vgmstream-win64/vgmstream-cli.exe"
        original = ROOT / row["original_wem_path"]
        if sha256(original.read_bytes()) != row["original_payload_sha256"]:
            raise ValueError("Original WEM checksum changed")
        decoded = wav.with_suffix('.decode-once.wav')
        # smpl describes runtime looping; offline analysis needs one physical stream.
        subprocess.run([str(vgm), "-i", "-o", str(decoded), str(original)], capture_output=True, check=True)
        if profile(decoded) != expected:
            raise ValueError(f"Original decoder profile mismatch for {row['audio_hash_en']}: {profile(decoded)} != {expected}")
        decoded.replace(wav)
        row['original_decode_policy'] = 'Physical stream once; vgmstream -i; original loop markers retained in WEM'
    if row["channels"] != 1:
        from .surround import original_profile
        original_profile(row, wav)
        return
    rate, samples = read_wav(wav)
    if rate != row["sample_rate"] or len(samples) != row["sample_count"]:
        raise ValueError("Original decoder profile mismatch")
    peak = max(map(abs, samples))
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    if not peak or not rms:
        raise ValueError("Original speech is silent")
    row.update(original_wav_path=str(wav.relative_to(ROOT)), original_peak_dbfs=20 * math.log10(peak / 32768),
               original_rms_dbfs=20 * math.log10(rms / 32768))


def ready_captioned_count(store, ready):
    """Use immutable original text, excluding newly recovered captionless speech."""
    from .dlge import text_fields
    missing = store.database.execute(
        "SELECT json_extract(document,'$.text_raw_en') FROM resources "
        "WHERE status='offline_final_wem_validated' "
        "AND COALESCE(json_extract(document,'$.text_pl_original'),'')=''"
    ).fetchall()
    extra = sum(not any(segment['text'].strip() for segment in text_fields(raw or '')['segments'])
                for raw, in missing)
    return ready - extra


def progress(store, chapter=None):
    from .billing import reconcile
    credit_qa = reconcile(store)
    from .deployment import active_install
    chapter = chapter or chapter_config()["active_chapter"]
    total = store.database.execute("SELECT COUNT(*) FROM chapter_resources WHERE chapter=?", (chapter,)).fetchone()[0]
    ready = store.database.execute("SELECT COUNT(*) FROM resources WHERE status='offline_final_wem_validated'").fetchone()[0]
    ready_captioned = ready_captioned_count(store, ready)
    rows = store.database.execute("SELECT r.scene,r.status FROM chapter_resources c JOIN resources r ON r.audio_hash=c.audio_hash WHERE c.chapter=? ORDER BY c.ordinal", (chapter,)).fetchall()
    chapter_ready = sum(status == "offline_final_wem_validated" for _, status in rows)
    chapter_preserved = sum(status in ("preserved_foreign_language", "preserved_nonverbal") for _, status in rows)
    contiguous = 0
    last_scene = None
    for scene, status in rows:
        if status not in ("offline_final_wem_validated", "preserved_foreign_language", "preserved_nonverbal"):
            break
        contiguous += 1
        last_scene = scene
    denominator = json.loads((ROOT / "docs/COVERAGE.json").read_text(encoding="utf-8"))["unique_audio_with_en_or_pl_text"]
    installed = active_install()
    installed_lines = 0
    if installed:
        bundle = json.loads(Path(installed["bundle"]).read_text(encoding="utf-8"))
        installed_lines = sum(item.get("type", "WWES") == "WWES" for item in bundle["resources"])
    preserved = store.database.execute("SELECT COUNT(*) FROM resources WHERE status IN ('preserved_foreign_language','preserved_nonverbal')").fetchone()[0]
    report = {"status": "campaign_in_progress", "updated_utc": datetime.now(timezone.utc).isoformat(),
              "ready_audio": ready, "installed_audio": installed_lines,
              "active_install_id": installed["install_id"] if installed else None,
              "preserved_audio": preserved, "active_chapter": chapter, "current_chapter_ready_audio": chapter_ready,
              "current_mission_total": total,
              "current_mission_scope": "verified mapped audio; unknown layouts and unclassified audio are excluded",
              "current_chapter_preserved_audio": chapter_preserved,
              "current_chapter_processed_audio": chapter_ready + chapter_preserved,
              "current_mission_percent": round((chapter_ready + chapter_preserved) / max(total, 1) * 100, 2),
              "current_english_dub_percent": round(chapter_ready / max(total - chapter_preserved, 1) * 100, 2), "known_game_audio_total": denominator,
              "known_game_audio_percent": round(ready_captioned / denominator * 100, 3),
              "known_captioned_ready_audio": ready_captioned,
              "additional_ready_audio_without_original_caption": ready - ready_captioned,
              "known_game_audio_measurement": "Original EN/PL-captioned corpus; recovered captionless speech counted separately",
              "chronological_prefix_audio": contiguous,
              "last_contiguous_scene": last_scene, "usage": store.usage(),
              "credit_reconciliation": credit_qa,
              "billing": {"mode": "existing_subscription_included_credits_only", "additional_charges_initiated_pln": 0,
                          "usage_value_is_estimate": True, "price_source_date": store.policy.get("pricing_verified_date", "2026-10-14")},
              "engine_validation": "opening WWES user confirmed; subsequent lines require gameplay feedback"}
    if chapter_config()["chapters"].get(chapter, {}).get("scope_kind") in ("reactive", "native_library"):
        report.update(processing_order="library_label_not_story_chronology",
                      chronological_prefix_audio=None, last_contiguous_scene=None,
                      processed_library_prefix_audio=contiguous, last_contiguous_library_group=last_scene)
    save_json(ROOT / "docs/CAMPAIGN_PROGRESS.json", report)
    return report


def compatible_retake(previous, current):
    """Allow text adaptation without reusing a different performance context."""
    def context(entry):
        identity = entry.get("identity")
        if not identity or not isinstance(identity.get("request", {}).get("text"), str):
            return None
        request = identity["request"]
        return ({key: value for key, value in identity.items() if key not in ("request", "pronunciation")},
                {key: value for key, value in request.items() if key != "text"},
                re.findall(r"\[[^\]]+\]", request["text"]))
    before, after = context(previous), context(current)
    return before is not None and after is not None and before == after


def run(lines=25, chapter=None, *, audio_hashes=None):
    store = initialize()
    processed = 0
    try:
        chapter = chapter or chapter_config()["active_chapter"]
        ensure_chapter(store, chapter)
        if audio_hashes is None:
            rows = store.database.execute("SELECT r.document FROM chapter_resources c JOIN resources r ON r.audio_hash=c.audio_hash WHERE c.chapter=? ORDER BY c.ordinal", (chapter,)).fetchall()
        else:
            # A bounded repair list may include earlier chapters without resuming them.
            rows = []
            for audio_hash in dict.fromkeys(audio_hashes):
                found = store.database.execute("SELECT document FROM resources WHERE audio_hash=?", (audio_hash,)).fetchone()
                if found is None:
                    raise ValueError("Unknown repair audio hash")
                rows.append(found)
        for (document,) in rows:
            row = json.loads(document)
            # The original first-mission manifest predates chapter IDs. Migrate
            # its pending rows so the same reviewed quiet-fit rules apply.
            if not row.get("chapter_id") and row.get("mission_id") == "clover":
                row["chapter_id"] = "clover"
            if row["qa_status"] in ("preserved_foreign_language", "preserved_nonverbal"):
                continue
            if row["qa_status"] == "offline_final_wem_validated":
                if sha256((ROOT / row["wem_path"]).read_bytes()) != row["wem_sha256"]:
                    raise ValueError("Ready campaign WEM changed")
                continue
            if processed >= lines:
                break
            if row["qa_status"] == "needs_agent_script_review":
                report = progress(store, chapter)
                report["needs_script_review"] = {key: row.get(key) for key in ("audio_hash_en", "scene_id", "text_en", "text_pl_original", "text_source_script", "text_raw_source")}
                return report
            original_wav(row)
            cues = row.get("original_markers", {}).get("cue ", {}).get("points", [])
            if row.get("chapter_id") and cues and all(point["sample_offset"] == 0 for point in cues) and "smpl" not in row["original_markers"]:
                row.setdefault("available_tail_extension_seconds", 0.25)
                if len(row["segments_pl"]) == 1:
                    row.setdefault("edge_padding_reduction_seconds", 0.015)
            casting = ROOT / "config/campaign-casting.json"
            result = generate(row, store=store, casting_path=casting if casting.exists() else CASTING, with_timestamps=True)
            row.update(generation_status="generated", generated_audio_path=result["audio_path"],
                       request_id=result.get("request_id"), tts_cache_key=result["cache_key"],
                       alignment_path=result.get("alignment_path"))
            save_row(store, row)
            try:
                alternatives = [entry for entry in store.load(row["audio_hash_en"])["entries"]
                                if entry["status"] == "succeeded" and entry.get("alignment_path")
                                and entry["cache_key"] != row["tts_cache_key"]
                                and entry["identity"]["voice_id"] == row["voice_id"]
                                and entry["identity"]["request"]["model_id"] == row["model_id"]
                                and entry["identity"]["request"]["voice_settings"] == row["settings"]
                                and compatible_retake(entry, result)]
                if row.get("channels", 1) != 1:
                    from .surround import prepare as prepare_surround
                    prepared = prepare_surround(row, ROOT / row["generated_audio_path"], alternatives)
                elif len(row["segments_pl"]) > 1:
                    if not row.get("alignment_path"):
                        raise ValueError("Recovered multi-segment audio needs alignment before fitting; no paid retry")
                    prepared = prepare_segments(row, ROOT / row["generated_audio_path"], cached_takes=alternatives)
                else:
                    try:
                        prepared = fit_audio(row, ROOT / row["generated_audio_path"])
                    except ValueError as timing_error:
                        from .shared_audio import prepare_shared
                        prepared = prepare_shared(row, ROOT / row["generated_audio_path"], timing_error)
                final = encode(row, prepared, ARTIFACTS / "campaign" / "final")
            except ValueError as error:
                row["qa_status"] = "needs_agent_adaptation"
                row["qa_error"] = str(error)
                save_row(store, row)
                report = progress(store, chapter)
                report["needs_attention"] = {key: row.get(key) for key in
                                             ("audio_hash_en", "scene_id", "scene_line_number", "speaker_id", "text_en",
                                              "text_pl_original", "text_pl_tts", "duration", "timing_qa", "segment_timing_qa", "qa_error", "alignment_path")}
                return report
            row.update(prepared_audio_path=str(prepared.relative_to(ROOT)), wem_path=str(final.relative_to(ROOT)),
                       wem_sha256=sha256(final.read_bytes()), qa_status="offline_final_wem_validated",
                       install_status="awaiting_install")
            row.pop('qa_error', None)
            save_row(store, row)
            processed += 1
            print(json.dumps({"completed": processed, "scene": row["scene_id"], "line": row["scene_line_number"],
                              "usage_value_pln": store.usage()["estimated_usage_value_pln"]}, ensure_ascii=False), flush=True)
        return progress(store, chapter)
    finally:
        store.close()
