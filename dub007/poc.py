"""Resumable generation, WEM validation and separate-patch packaging."""
import json
from pathlib import Path
import re

from .audio import encode, prepare
from .common import ARTIFACTS, ROOT, save_json, sha256
from .installer import checked_definition, fingerprints, partition_level
from .patching import write_patch
from .tts import generate, usage
from .subtitles import resource as subtitle_resource


def bundle_signature(rows):
    inputs = [{key: row.get(key) for key in ("audio_hash_en", "wem_sha256", "line_id", "leaf_index",
                                            "text_pl_display", "display_segments", "original_dlge_payload_sha256",
                                            "original_payload_sha256", "raw_resource_refs_hex", "sysmem", "vidmem")}
              for row in rows]
    return sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode())


def bundle(rows):
    if not rows or any(row.get("qa_status") != "offline_final_wem_validated" for row in rows):
        raise ValueError("Every bundled resource must pass final WEM QA")
    directory = ARTIFACTS / "poc" / "bundle"
    directory.mkdir(parents=True, exist_ok=True)
    patches = []
    plain = checked_definition((ROOT / "Runtime" / "packagedefinition.txt").read_bytes())[2]
    for archive in sorted({row["active_archive"] for row in rows}):
        match = re.fullmatch(r"chunk(\d+)\.rpkg", archive)
        if not match:
            raise ValueError("PoC only packages verified base archives")
        chunk = int(match[1])
        partition = "super" if chunk == 0 else "base" if chunk == 1 else None
        if partition is None:
            raise ValueError("Unknown chunk/partition mapping")
        _, current = partition_level(plain, partition)
        occupied = [int(found[1]) for path in (ROOT / "Runtime").glob(f"chunk{chunk}patch*.rpkg")
                    if (found := re.fullmatch(r"chunk\d+patch(\d+)\.rpkg", path.name))]
        number = max([current, *occupied]) + 1
        path = directory / f"chunk{chunk}patch{number}.rpkg"
        resources = []
        for row in rows:
            if row["active_archive"] == archive:
                payload = (ROOT / row["wem_path"]).read_bytes()
                if sha256(payload) != row["wem_sha256"]:
                    raise ValueError("Validated WEM changed")
                resources.append({"hash": row["audio_hash_en"], "type": row["resource_type"], "payload": payload,
                                  "refs": bytes.fromhex(row["raw_resource_refs_hex"]), "sysmem": row["sysmem"], "vidmem": row["vidmem"]})
                caption = subtitle_resource(row)
                if caption:
                    resources.append(caption)
        validation = write_patch(path, resources, chunk, number)
        patches.append({"filename": path.name, "sha256": sha256(path.read_bytes()), "patch_number": number,
                        "chunk": chunk, "partition": partition, "validation": validation})
    report = {"status": "offline_validated", "runtime_status": "awaiting_in_game_validation",
              "input_signature": bundle_signature(rows),
              "base_archive_fingerprints": fingerprints(ROOT), "patches": patches,
              "resources": [{"archive": row["active_archive"], "hash": row["audio_hash_en"], "type": "WWES",
                             "original_payload_sha256": row["original_payload_sha256"], "wem_sha256": row["wem_sha256"]} for row in rows]}
    for row in rows:
        if row.get("subtitle_patch"):
            caption = row["subtitle_patch"]
            report["resources"].append({"archive": row["archive"], "hash": row["line_id"], "type": "DLGE",
                                        "original_payload_sha256": caption["original_payload_sha256"]})
    save_json(directory / "bundle.json", report)
    return report


def run(lines=10):
    if not 1 <= lines <= 10:
        raise ValueError("PoC accepts 1 to 10 lines")
    checkpoint = ARTIFACTS / "wwise" / "roundtrip.json"
    if not checkpoint.exists() or json.loads(checkpoint.read_text(encoding="utf-8"))["status"] != "offline_roundtrip_passed":
        raise RuntimeError("Run encoder-check before any paid TTS")
    path = ARTIFACTS / "poc" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for row in manifest["rows"][:lines]:
        if row.get("qa_status") == "offline_final_wem_validated":
            if sha256((ROOT / row["wem_path"]).read_bytes()) != row["wem_sha256"]:
                raise ValueError("Final WEM changed")
            continue
        generation = generate(row)
        row.update(generation_status="generated", generated_audio_path=generation["audio_path"],
                   request_id=generation.get("request_id"), tts_cache_key=generation["cache_key"])
        manifest["tts_usage"] = usage()
        manifest["tts_characters_sent"] = manifest["tts_usage"]["submitted_characters"]
        save_json(path, manifest)
        try:
            prepared = prepare(row, ROOT / row["generated_audio_path"])
            wem = encode(row, prepared)
        except ValueError:
            save_json(path, manifest)
            raise
        row.update(wem_path=str(wem.relative_to(ROOT)), wem_sha256=sha256(wem.read_bytes()),
                   prepared_audio_path=str(prepared.relative_to(ROOT)), qa_status="offline_final_wem_validated")
        save_json(path, manifest)
    selected = manifest["rows"][:lines]
    bundle_path = ARTIFACTS / "poc" / "bundle" / "bundle.json"
    existing = json.loads(bundle_path.read_text(encoding="utf-8")) if bundle_path.exists() else None
    if existing and existing.get("input_signature") == bundle_signature(selected):
        if any(sha256((bundle_path.parent / patch["filename"]).read_bytes()) != patch["sha256"] for patch in existing["patches"]):
            raise ValueError("Cached bundle patch changed")
        report = existing
    else:
        report = bundle(selected)
    manifest["status"] = "awaiting_in_game_validation"
    manifest["tts_usage"] = usage()
    manifest["tts_characters_sent"] = manifest["tts_usage"]["submitted_characters"]
    save_json(path, manifest)
    return {"status": manifest["status"], "lines": lines, "usage": usage(), "patches": report["patches"]}
