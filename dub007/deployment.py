"""Immutable cumulative bundles with journaled replacement of our own campaign."""
import json
from pathlib import Path
import re
import uuid

from .campaign import initialize, progress
from .common import ARTIFACTS, ROOT, save_json, sha256
from .installer import checked_definition, partition_level, set_partition_level, fingerprints, install, rollback, closed_game, plan
from .patching import write_patch
from .subtitles import resources as subtitles

CURRENT = ARTIFACTS / "campaign" / "current-install.json"
DEPLOYMENT = ARTIFACTS / "campaign" / "deployment.json"


def recover_pending(*, dry_run=False):
    """Reconcile a crash between replacing our patch and publishing its pointer."""
    if not DEPLOYMENT.exists():
        return None
    deployment = json.loads(DEPLOYMENT.read_text(encoding="utf-8"))
    if deployment["status"] in ("installed", "recovered_clean", "recovered_installed"):
        return None
    if dry_run:
        return {"status": "pending_deployment_recovery", "new_install_id": deployment["new_install_id"],
                "command": "campaign-install"}
    closed_game()
    journal_path = ARTIFACTS / "backups" / deployment["new_install_id"] / "journal.json"
    if journal_path.exists():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        if Path(journal["game_root"]).resolve() != ROOT.resolve():
            raise ValueError("Pending journal belongs to another game installation")
        if journal["status"] == "installed":
            if fingerprints(ROOT) != journal["base_archive_fingerprints"]:
                raise ValueError("Game changed during pending campaign deployment")
            plain = checked_definition((ROOT / "Runtime/packagedefinition.txt").read_bytes())[2]
            for change in journal["changes"]:
                if partition_level(plain, change["partition"])[1] < change["after"]:
                    raise ValueError("Pending installed campaign is no longer activated")
            for patch in journal["patches"]:
                if sha256((ROOT / "Runtime" / patch["filename"]).read_bytes()) != patch["sha256"]:
                    raise ValueError("Pending installed campaign patch changed")
            save_json(CURRENT, journal)
            save_json(ARTIFACTS / "poc/current-install.json", journal)
            deployment["status"] = "recovered_installed"
            save_json(DEPLOYMENT, deployment)
            return deployment
        if journal["status"] != "rolled_back":
            rollback(journal["install_id"])
    old_id = deployment.get("old_install_id")
    if old_id:
        old_path = ARTIFACTS / "backups" / old_id / "journal.json"
        old_journal = json.loads(old_path.read_text(encoding="utf-8"))
        if old_journal["status"] == "rolling_back":
            rollback(old_id)
    deployment["status"] = "recovered_clean"
    save_json(DEPLOYMENT, deployment)
    return deployment


def active_install():
    for pointer in (CURRENT, ARTIFACTS / "poc" / "current-install.json"):
        if not pointer.exists():
            continue
        candidate = json.loads(pointer.read_text(encoding="utf-8"))
        journal = ARTIFACTS / "backups" / candidate["install_id"] / "journal.json"
        if journal.exists() and json.loads(journal.read_text(encoding="utf-8"))["status"] != "rolled_back":
            return candidate
    return None


def baseline_definition(current):
    raw = (ROOT / "Runtime/packagedefinition.txt").read_bytes()
    if not current:
        return raw
    codec, header, plain = checked_definition(raw)
    own_names = {patch["filename"] for patch in current["patches"]}
    for change in current["changes"]:
        _, level = partition_level(plain, change["partition"])
        if level == change["after"]:
            foreign = [int(match[1]) for path in (ROOT / "Runtime").glob(f"chunk{change['chunk']}patch*.rpkg")
                       if path.name not in own_names and (match := re.fullmatch(r"chunk\d+patch(\d+)\.rpkg", path.name))]
            plain = set_partition_level(plain, change["partition"], max([change["before"], *foreign]))
    return codec.encrypt(header, plain)


def build(store):
    rows = [json.loads(document) for (document,) in store.database.execute("SELECT document FROM resources WHERE status='offline_final_wem_validated' ORDER BY position")]
    if not rows:
        raise ValueError("No campaign audio has passed final QA")
    current = active_install()
    own_names = {patch["filename"] for patch in current["patches"]} if current else set()
    definition = checked_definition(baseline_definition(current))[2]
    resources = []
    for row in rows:
        payload = (ROOT / row["wem_path"]).read_bytes()
        if sha256(payload) != row["wem_sha256"]:
            raise ValueError("Campaign WEM changed before packaging")
        resources.append({"hash": row["audio_hash_en"], "type": "WWES", "payload": payload,
                          "refs": bytes.fromhex(row["raw_resource_refs_hex"]), "sysmem": row["sysmem"], "vidmem": row["vidmem"],
                          "archive": row["active_archive"], "original_payload_sha256": row["original_payload_sha256"]})
    resources.extend(subtitles(rows))
    slots = {}
    for archive in sorted({item["archive"] for item in resources}):
        chunk = int(re.fullmatch(r"chunk(\d+)\.rpkg", archive)[1])
        partition = "super" if chunk == 0 else "base" if chunk == 1 else None
        if partition is None:
            raise ValueError("Unknown campaign partition")
        _, level = partition_level(definition, partition)
        occupied = [int(match[1]) for file in (ROOT / "Runtime").glob(f"chunk{chunk}patch*.rpkg")
                    if file.name not in own_names and (match := re.fullmatch(r"chunk\d+patch(\d+)\.rpkg", file.name))]
        slots[archive] = (chunk, max([level, *occupied]) + 1, partition)
    signature = sha256(json.dumps({"payloads": [[item["hash"], sha256(item["payload"])] for item in resources], "slots": slots}, sort_keys=True).encode())
    folder = ARTIFACTS / "campaign" / "bundles" / signature
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "bundle.json"
    if path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
        for patch in report["patches"]:
            if sha256((folder / patch["filename"]).read_bytes()) != patch["sha256"]:
                raise ValueError("Immutable cached campaign bundle changed")
        return path, rows
    patches = []
    for archive in sorted({item["archive"] for item in resources}):
        chunk, number, partition = slots[archive]
        patch = folder / f"chunk{chunk}patch{number}.rpkg"
        validation = write_patch(patch, [item for item in resources if item["archive"] == archive], chunk, number)
        patches.append({"filename": patch.name, "sha256": sha256(patch.read_bytes()), "chunk": chunk,
                        "patch_number": number, "partition": partition, "validation": validation})
    report = {"status": "offline_validated", "runtime_status": "new_lines_awaiting_in_game_validation",
              "mechanism": "separate WWES patch user verified", "patches": patches,
              "base_archive_fingerprints": fingerprints(ROOT), "signature": signature,
              "audio_lines": len(rows), "resources": [{key: item[key] for key in ("archive", "hash", "type", "original_payload_sha256")} for item in resources]}
    save_json(path, report)
    return path, rows


def record_installed_rows(store, rows):
    """Merge installation metadata without reverting a newer script or retake."""
    with store.database:
        # Acquire the writer lock before reading current documents.
        store.database.execute("UPDATE resources SET status=status WHERE 0")
        for snapshot in rows:
            found = store.database.execute("SELECT document FROM resources WHERE audio_hash=?",
                                           (snapshot["audio_hash_en"],)).fetchone()
            if found is None:
                continue
            current = json.loads(found[0])
            if current.get("qa_status") != "offline_final_wem_validated" or any(
                    current.get(key) != snapshot.get(key) for key in
                    ("wem_sha256", "text_pl_display", "display_segments", "reviewed_subtitle_segments")):
                continue
            current["install_status"] = "installed_new_lines_awaiting_in_game_validation"
            if "subtitle_patches" in snapshot:
                current["subtitle_patches"] = snapshot["subtitle_patches"]
            store.database.execute("UPDATE resources SET document=? WHERE audio_hash=?",
                                   (json.dumps(current, ensure_ascii=False), current["audio_hash_en"]))


def run(dry_run=False):
    recovery = recover_pending(dry_run=dry_run)
    if recovery and recovery["status"] == "pending_deployment_recovery":
        return recovery
    store = initialize()
    try:
        path, rows = build(store)
        previous = active_install()
        if previous and previous["bundle"] == str(path.resolve()):
            for patch in previous["patches"]:
                if sha256((ROOT / "Runtime" / patch["filename"]).read_bytes()) != patch["sha256"]:
                    raise ValueError("Current campaign patch changed")
            return {"status": "already_installed", "audio_lines": len(rows), "install_id": previous["install_id"]}
        if dry_run:
            preview = plan(path, definition_override=baseline_definition(previous),
                           ignored_existing={patch["filename"]: patch["sha256"] for patch in previous["patches"]} if previous else None)[0]
            return {"status": "dry_run", "bundle": str(path), "audio_lines": len(rows),
                    "replaces_our_install_id": previous["install_id"] if previous else None, "validated_plan": preview}
        closed_game()
        identifier = uuid.uuid4().hex
        deployment = {"status": "prepared", "new_install_id": identifier, "bundle": str(path),
                      "old_install_id": previous["install_id"] if previous else None}
        journal_path = DEPLOYMENT
        save_json(journal_path, deployment)
        if previous:
            rollback(previous["install_id"])
        deployment["status"] = "old_removed"
        save_json(journal_path, deployment)
        installed = install(path, install_id=identifier)
        save_json(CURRENT, installed)
        save_json(ARTIFACTS / "poc" / "current-install.json", installed)
        record_installed_rows(store, rows)
        deployment["status"] = "installed"
        save_json(journal_path, deployment)
        report = progress(store)
        report.update(status="installed", install_id=identifier, installed_audio_lines=len(rows))
        save_json(ROOT / "docs/CAMPAIGN_PROGRESS.json", report)
        return report
    finally:
        store.close()
