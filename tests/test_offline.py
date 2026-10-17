"""Regression tests for actual archive preservation and interrupted rollback."""
import json
from pathlib import Path
import struct
import tempfile
import unittest

from dub007.archive import iter_entries, parse_tables
from dub007.common import ROOT, save_json, sha256, upstream
from dub007.dependencies import safe_extract
from dub007.dlge import parse
from dub007.installer import fingerprints, install, rollback
from dub007.patching import write_patch


def base_archive(path, resources):
    info = b"".join(row["type"].encode()[::-1] + struct.pack("<IIII", len(row["refs"]), len(row["payload"]), row["sysmem"], row["vidmem"]) + row["refs"] for row in resources)
    header = b"2KPR" + bytes([1, 0, 0, 0, 0, 0, 0, 0x78, 0x78]) + struct.pack("<III", len(resources), 20 * len(resources), len(info))
    offset, hashes = len(header) + 20 * len(resources) + len(info), []
    for row in resources:
        hashes.append(struct.pack("<QQI", int(row["hash"], 16), offset, 0))
        offset += len(row["payload"])
    path.write_bytes(header + b"".join(hashes) + info + b"".join(row["payload"] for row in resources))


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dub007-offline-")
        self.root = Path(self.temp.name)
        self.game = self.root / "Game"
        self.runtime = self.game / "Runtime"
        self.runtime.mkdir(parents=True)
        self.backups = self.root / "backups"
        self.bundle_dir = self.root / "bundle"
        self.bundle_dir.mkdir()
        self.resources = [{"hash": "0100000000000001", "type": "WWES", "refs": b"", "sysmem": 123, "vidmem": 0,
                           "payload": b"original synthetic resource"}]
        base_archive(self.runtime / "chunk0.rpkg", self.resources)
        codec = upstream("pkgdef")
        self.header = bytes.fromhex("b7e2ea00545b6b8711bd6fe84d6ad4bf")
        self.plain = b"@partition name=super parent=none type=standard patchlevel=0\r\n@partition name=base parent=super type=standard patchlevel=0\r\n"
        self.original = codec.encrypt(self.header, self.plain)
        (self.runtime / "packagedefinition.txt").write_bytes(self.original)

    def tearDown(self):
        self.temp.cleanup()

    def make_bundle(self, resources=None):
        if resources is not None:
            self.resources = resources
            base_archive(self.runtime / "chunk0.rpkg", resources)
        modified = [{**row, "payload": row["payload"] + b"replacement"} for row in self.resources]
        patch = self.bundle_dir / "chunk0patch1.rpkg"
        write_patch(patch, modified, 0, 1)
        bundle = {"status": "offline_validated", "base_archive_fingerprints": fingerprints(self.game),
                  "resources": [{"archive": "chunk0.rpkg", "hash": row["hash"], "original_payload_sha256": sha256(row["payload"])} for row in self.resources],
                  "patches": [{"filename": patch.name, "sha256": sha256(patch.read_bytes()), "patch_number": 1, "chunk": 0, "partition": "super"}]}
        path = self.bundle_dir / "bundle.json"
        save_json(path, bundle)
        return path

    def install(self, bundle, **kwargs):
        return install(bundle, game_root=self.game, backup_root=self.backups, **kwargs)

    def restore(self, identifier):
        return rollback(identifier, backup_root=self.backups, expected_game_root=self.game)

    def test_install_and_complete_rollback(self):
        bundle = self.make_bundle()
        before_archive = (self.runtime / "chunk0.rpkg").read_bytes()
        self.assertEqual(self.install(bundle, dry_run=True)["status"], "dry_run")
        self.assertFalse(self.backups.exists())
        journal = self.install(bundle)
        self.assertTrue((self.runtime / "chunk0patch1.rpkg").exists())
        self.assertEqual(self.restore(journal["install_id"])["status"], "rolled_back")
        self.assertEqual((self.runtime / "packagedefinition.txt").read_bytes(), self.original)
        self.assertEqual((self.runtime / "chunk0.rpkg").read_bytes(), before_archive)
        self.assertFalse((self.runtime / "chunk0patch1.rpkg").exists())

    def test_interruption_before_manifest_activation(self):
        with self.assertRaisesRegex(RuntimeError, "rollback --install-id"):
            self.install(self.make_bundle(), interrupt_after="patches_written")
        identifier = next(self.backups.iterdir()).name
        self.assertEqual((self.runtime / "packagedefinition.txt").read_bytes(), self.original)
        self.restore(identifier)
        self.assertFalse((self.runtime / "chunk0patch1.rpkg").exists())

    def test_preserve_later_mod_and_manifest_changes(self):
        journal = self.install(self.make_bundle())
        other = self.runtime / "chunk0patch2.rpkg"
        write_patch(other, self.resources, 0, 2)
        other_hash = sha256(other.read_bytes())
        codec = upstream("pkgdef")
        _, _, current = codec.decrypt((self.runtime / "packagedefinition.txt").read_bytes())
        later = current.replace(b"name=base parent=super type=standard patchlevel=0", b"name=base parent=super type=standard patchlevel=7") + b"// another mod\r\n"
        (self.runtime / "packagedefinition.txt").write_bytes(codec.encrypt(self.header, later))
        self.restore(journal["install_id"])
        result = codec.decrypt((self.runtime / "packagedefinition.txt").read_bytes())[2]
        self.assertIn(b"name=base parent=super type=standard patchlevel=7", result)
        self.assertIn(b"name=super parent=none type=standard patchlevel=2", result)
        self.assertIn(b"// another mod", result)
        self.assertEqual(sha256(other.read_bytes()), other_hash)

    def test_refuse_game_update(self):
        journal = self.install(self.make_bundle())
        archive = self.runtime / "chunk0.rpkg"
        archive.write_bytes(archive.read_bytes() + b"game update")
        with self.assertRaisesRegex(ValueError, "archive changes"):
            self.restore(journal["install_id"])
        self.assertTrue((self.runtime / "chunk0patch1.rpkg").exists())

    def test_refuse_changed_installed_patch(self):
        journal = self.install(self.make_bundle())
        patch = self.runtime / "chunk0patch1.rpkg"
        patch.write_bytes(patch.read_bytes() + b"other edit")
        with self.assertRaisesRegex(ValueError, "patch changed"):
            self.restore(journal["install_id"])
        self.assertTrue(patch.exists())

    def test_archive_filename_header_agreement(self):
        path = self.bundle_dir / "chunk0patch1.rpkg"
        write_patch(path, self.resources, 0, 1)
        bad = path.with_name("chunk0patch2.rpkg")
        bad.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "subheader disagree"):
            parse_tables(bad)
        with self.assertRaises(ValueError):
            write_patch(path, self.resources, 0, 310)

    def test_copy_of_real_wem_resources(self):
        manifest_path = ROOT / "artifacts" / "poc" / "manifest.json"
        if not manifest_path.exists():
            self.skipTest("prepare-poc must run first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        resources = [{"hash": row["audio_hash_en"], "type": "WWES", "refs": bytes.fromhex(row["raw_resource_refs_hex"]),
                      "sysmem": row["sysmem"], "vidmem": row["vidmem"], "payload": (ROOT / row["original_wem_path"]).read_bytes()}
                     for row in manifest["rows"][:3]]
        bundle = self.make_bundle(resources)
        before = (self.runtime / "chunk0.rpkg").read_bytes()
        journal = self.install(bundle)
        self.restore(journal["install_id"])
        self.assertEqual(before, (self.runtime / "chunk0.rpkg").read_bytes())
        self.assertEqual(self.original, (self.runtime / "packagedefinition.txt").read_bytes())

    def test_zip_path_escape_rejected(self):
        import zipfile
        malicious = self.root / "test.zip"
        with zipfile.ZipFile(malicious, "w") as archive:
            archive.writestr("..\\escape.txt", "escape")
        with self.assertRaises(ValueError):
            safe_extract(malicious, self.root / "extract")
        self.assertFalse((self.root / "escape.txt").exists())

    def test_raw_references_with_independent_toolbox_reader(self):
        references = struct.pack("<I", 0xC0000001) + b"\x5f" + struct.pack("<Q", 0x0100000000000044)
        resource = {**self.resources[0], "refs": references, "sysmem": 65535, "vidmem": 8192}
        patch = self.bundle_dir / "chunk0patch7.rpkg"
        write_patch(patch, [resource], 0, 7)
        index = upstream("rpkg_index").parse(str(patch))
        entry = index["entries"][0]
        with patch.open("rb") as stream:
            self.assertEqual(upstream("rpkg_read").read_resource(stream, entry), resource["payload"])
        self.assertEqual(entry["refs"], [(0x5f, "0100000000000044")])
        self.assertEqual((entry["sysmem"], entry["vidmem"]), (65535, 8192))

    def test_first_light_root_uses_global_random_switch_index(self):
        raw = struct.pack("<II", 0, 1)
        raw += struct.pack("<BIII", 2, 0, 0, 0) * 2
        raw += struct.pack("<BIII", 3, 0, 0, 0)
        raw += struct.pack("<H", 0x3002)
        result = parse(raw, [{"hash": "0100000000000001"}, {"hash": "0100000000000002"}])
        self.assertEqual(result["root_typed_index"], 0x3002)
        self.assertEqual(len(result["containers"]), 3)


if __name__ == "__main__":
    unittest.main()
