"""Journaled installation and surgical rollback of this mod's separate patches."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import zlib

from .archive import iter_entries, parse_tables, read_resource
from .common import ARTIFACTS, ROOT, save_json, sha256, upstream


def closed_game():
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True)
    for line in result.stdout.splitlines():
        name = line.split(",", 1)[0].strip('"').casefold()
        if "firstlight" in name or "first light" in name:
            raise RuntimeError("Close 007 First Light before install or rollback")


def durable_bytes(path, data):
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def checked_definition(data):
    from . import pkgdef_codec as codec
    header, crc, plain = codec.decrypt(data)
    if crc != zlib.crc32(plain) or codec.encrypt(header, plain) != data:
        raise ValueError("packagedefinition failed CRC or roundtrip")
    return codec, header, plain


def partition_level(plain, partition):
    expression = rb"(?m)(^@partition\s+name=" + re.escape(partition.encode()) + rb"\b[^\r\n]*\bpatchlevel=)(\d+)"
    matches = list(re.finditer(expression, plain))
    if len(matches) != 1:
        raise ValueError("Partition does not match verified packagedefinition syntax")
    return expression, int(matches[0][2])


def set_partition_level(plain, partition, level):
    expression, current = partition_level(plain, partition)
    return re.sub(expression, lambda match: match[1] + str(level).encode(), plain, count=1)


def fingerprints(game_root):
    return {path.name: {key: value for key, value in parse_tables(path)[-1].items() if key in ("size", "index_sha256")}
            for path in sorted((game_root / "Runtime").glob("chunk[0-9].rpkg"))}


def plan(bundle_path, game_root=ROOT, *, definition_override=None, ignored_existing=None):
    bundle_path = Path(bundle_path).resolve()
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if bundle.get("status") != "offline_validated":
        raise ValueError("Bundle has not passed offline validation")
    runtime = game_root / "Runtime"
    before = definition_override if definition_override is not None else (runtime / "packagedefinition.txt").read_bytes()
    codec, header, plain = checked_definition(before)
    after = plain
    changes, patches = [], []
    for item in bundle["patches"]:
        if not re.fullmatch(r"chunk\d+patch\d+\.rpkg", item["filename"]):
            raise ValueError("Invalid patch filename")
        source = (bundle_path.parent / item["filename"]).resolve()
        if not source.is_relative_to(bundle_path.parent):
            raise ValueError("Patch source escapes bundle directory")
        payload = source.read_bytes()
        if sha256(payload) != item["sha256"]:
            raise ValueError("Patch checksum mismatch")
        parse_tables(source)
        destination = runtime / item["filename"]
        if destination.exists():
            expected_existing = (ignored_existing or {}).get(item["filename"])
            if not expected_existing or sha256(destination.read_bytes()) != expected_existing:
                raise ValueError("Patch destination already exists; never overwrite another mod")
        _, old_level = partition_level(after, item["partition"])
        new_level = max(old_level, item["patch_number"])
        after = set_partition_level(after, item["partition"], new_level)
        changes.append({"partition": item["partition"], "before": old_level, "after": new_level,
                        "chunk": item["chunk"]})
        patches.append({"filename": item["filename"], "source": str(source), "sha256": item["sha256"]})
    if bundle.get("base_archive_fingerprints") != fingerprints(game_root):
        raise ValueError("Game archives differ from validated bundle")
    if not bundle.get("resources"):
        raise ValueError("Bundle does not describe source resources")
    for archive in {row["archive"] for row in bundle["resources"]}:
        if archive not in bundle["base_archive_fingerprints"]:
            raise ValueError("Resource archive is not a verified base archive")
        archive_path = runtime / archive
        wanted = {row["hash"]: row for row in bundle["resources"] if row["archive"] == archive}
        with archive_path.open("rb") as stream:
            for entry in iter_entries(archive_path, parse_tables(archive_path)):
                h, typ, _, off, flags, size, *_ = entry
                if h in wanted:
                    actual = read_resource(stream, {"offset": off, "flags": flags, "size": size})
                    expected = wanted.pop(h)
                    if typ != expected.get("type", "WWES") or sha256(actual) != expected["original_payload_sha256"]:
                        raise ValueError("Original resource checksum or type mismatch")
            if wanted:
                raise ValueError("Expected source resources are missing")
    return {"game_root": str(game_root.resolve()), "bundle": str(bundle_path), "patches": patches,
            "changes": changes, "before_sha256": sha256(before), "after_sha256": sha256(codec.encrypt(header, after)),
            "base_archive_fingerprints": bundle["base_archive_fingerprints"]}, before, codec.encrypt(header, after)


def install(bundle_path, *, dry_run=False, game_root=ROOT, backup_root=ARTIFACTS / "backups", interrupt_after=None, install_id=None):
    if not dry_run:
        closed_game()
    installation, before, after = plan(bundle_path, game_root)
    if dry_run:
        return {"status": "dry_run", **installation}
    required = sum(Path(item["source"]).stat().st_size for item in installation["patches"]) + len(before) * 4 + 1024 * 1024
    if shutil.disk_usage(game_root).free < required:
        raise RuntimeError("Insufficient free space for patch, journal and manifest staging")
    identifier = install_id or uuid.uuid4().hex
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("Invalid explicit installation ID")
    backup = backup_root / identifier
    backup.mkdir(parents=True, exist_ok=False)
    durable_bytes(backup / "packagedefinition.original.bin", before)
    durable_bytes(backup / "packagedefinition.installed.bin", after)
    journal = {"install_id": identifier, "status": "prepared", **installation}
    save_json(backup / "journal.json", journal)
    runtime = game_root / "Runtime"
    closed_game()
    try:
        for item in installation["patches"]:
            destination = runtime / item["filename"]
            if destination.exists():
                raise ValueError("Patch appeared after preflight; refusing overwrite")
            temporary = runtime / f".dub007-{identifier}-{item['filename']}.tmp"
            durable_bytes(temporary, Path(item["source"]).read_bytes())
            if sha256(temporary.read_bytes()) != item["sha256"]:
                raise ValueError("Staged patch checksum mismatch")
            # rename on Windows refuses an existing destination.
            temporary.rename(destination)
            if sha256(destination.read_bytes()) != item["sha256"]:
                raise ValueError("Installed patch readback mismatch")
        journal["status"] = "patches_written"
        save_json(backup / "journal.json", journal)
        if interrupt_after == "patches_written":
            raise RuntimeError("Injected interruption after patch write")
        definition = runtime / "packagedefinition.txt"
        closed_game()
        if sha256(definition.read_bytes()) != installation["before_sha256"]:
            raise ValueError("Manifest changed during install; rollback journal remains available")
        temporary = runtime / f".dub007-{identifier}-packagedefinition.tmp"
        durable_bytes(temporary, after)
        os.replace(temporary, definition)
        if sha256(definition.read_bytes()) != installation["after_sha256"]:
            raise ValueError("Manifest readback mismatch")
        journal["status"] = "installed"
        save_json(backup / "journal.json", journal)
        return journal
    except Exception as error:
        raise RuntimeError(f"Install interrupted; rollback --install-id {identifier}. {error}") from None


def rollback(identifier, *, backup_root=ARTIFACTS / "backups", expected_game_root=ROOT):
    if not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("Invalid install ID")
    closed_game()
    backup = backup_root / identifier
    journal = json.loads((backup / "journal.json").read_text(encoding="utf-8"))
    if journal["status"] == "rolled_back":
        return journal
    game_root = Path(journal["game_root"])
    if game_root.resolve() != expected_game_root.resolve():
        raise ValueError("Journal belongs to another installation")
    if fingerprints(game_root) != journal["base_archive_fingerprints"]:
        raise ValueError("Game update or archive changes detected; refusing stale rollback")
    runtime = game_root / "Runtime"
    original = (backup / "packagedefinition.original.bin").read_bytes()
    if sha256(original) != journal["before_sha256"]:
        raise ValueError("Original manifest backup checksum mismatch")
    current = (runtime / "packagedefinition.txt").read_bytes()
    codec, header, plain = checked_definition(current)
    original_header = checked_definition(original)[1]
    if header != original_header:
        raise ValueError("Manifest header changed; refusing incompatible rollback")
    own_names = {item["filename"] for item in journal["patches"]}
    for item in journal["patches"]:
        destination = runtime / item["filename"]
        if destination.exists() and sha256(destination.read_bytes()) != item["sha256"]:
            raise ValueError("Installed patch changed; refusing to delete another modification")
    if current == original:
        restored = current
    else:
        for change in journal["changes"]:
            _, level = partition_level(plain, change["partition"])
            other_levels = [int(match[1]) for path in runtime.glob(f"chunk{change['chunk']}patch*.rpkg")
                            if path.name not in own_names
                            and (match := re.fullmatch(r"chunk\d+patch(\d+)\.rpkg", path.name))]
            if level == change["after"]:
                plain = set_partition_level(plain, change["partition"], max([change["before"], *other_levels]))
        restored = codec.encrypt(header, plain)
    journal["status"] = "rolling_back"
    journal["rollback_target_manifest_sha256"] = sha256(restored)
    save_json(backup / "journal.json", journal)
    # Restore activation before deleting payloads so interrupted rollback is recoverable.
    temporary = runtime / f".dub007-{identifier}-rollback.tmp"
    closed_game()
    durable_bytes(temporary, restored)
    os.replace(temporary, runtime / "packagedefinition.txt")
    if sha256((runtime / "packagedefinition.txt").read_bytes()) != sha256(restored):
        raise ValueError("Rollback manifest readback mismatch")
    for item in journal["patches"]:
        destination = runtime / item["filename"]
        if destination.exists():
            if sha256(destination.read_bytes()) != item["sha256"]:
                raise ValueError("Patch changed during rollback")
            destination.unlink()
        staged = runtime / f".dub007-{identifier}-{item['filename']}.tmp"
        if staged.exists():
            staged_data = staged.read_bytes()
            source = Path(item["source"])
            complete = sha256(staged_data) == item["sha256"]
            known_partial = source.is_file() and source.read_bytes().startswith(staged_data)
            if not complete and not known_partial:
                raise ValueError("Unrecognized staged patch; leave for inspection")
            staged.unlink()
    journal["status"] = "rolled_back"
    journal["restored_manifest_sha256"] = sha256(restored)
    save_json(backup / "journal.json", journal)
    return journal
