import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import zlib
from unittest.mock import patch

from dub007.common import sha256, save_json
from dub007.installer import checked_definition, fingerprints, install, rollback
from dub007 import pkgdef_codec
from dub007.patching import write_patch
from dub007.release import make_delta, apply_delta, validate_package, build_recipient_bundle, operation_lock


class ReleaseTests(unittest.TestCase):
    def test_second_recipient_operation_is_blocked_and_lock_is_reusable(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with operation_lock(state):
                with self.assertRaisesRegex(RuntimeError, 'inna instalacja'):
                    with operation_lock(state):
                        self.fail('Second simultaneous installer acquired the lock')
            with operation_lock(state):
                self.assertTrue((state/'operation.lock').is_file())

    def test_delta_reconstructs_insertions_deletions_and_shifted_records(self):
        original = bytes(range(256))*4
        changed = original[:31]+b'POLSKI TEKST'+original[53:700]+b'end'
        self.assertEqual(apply_delta(original, make_delta(original, changed)), changed)
        for source, final in ((b'', b'new'), (b'old', b''), (original, original)):
            self.assertEqual(apply_delta(source, make_delta(source, final)), final)

    def test_delta_refuses_wrong_source_and_corrupt_copy_or_result(self):
        original = b'original source resource'*4
        delta = make_delta(original, original+b' changed')
        with self.assertRaisesRegex(ValueError, 'original game resource'):
            apply_delta(b'another build', delta)
        document = json.loads(zlib.decompress(delta))
        document['operations'] = [['copy', 0, len(original)+1]]
        with self.assertRaisesRegex(ValueError, 'outside original'):
            apply_delta(original, zlib.compress(json.dumps(document).encode()))
        document['operations'] = [['data', base64.b64encode(b'wrong').decode()]]
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            apply_delta(original, zlib.compress(json.dumps(document).encode()))

    def test_package_rejects_hidden_files_and_path_traversal(self):
        for path in ('../private', 'resources/unlisted.wem'):
            with tempfile.TemporaryDirectory() as folder:
                package = Path(folder)/'mod.zip'
                with zipfile.ZipFile(package, 'w') as archive:
                    archive.writestr('release.json', json.dumps({'format': 1, 'resources': []}))
                    archive.writestr(path, b'bad')
                with zipfile.ZipFile(package) as archive:
                    with self.assertRaisesRegex(ValueError, 'Unlisted'):
                        validate_package(archive)

    def test_recipient_build_install_and_rollback_preserve_existing_other_mod(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            game = root/'game'
            runtime = game/'Runtime'
            runtime.mkdir(parents=True)
            archive = runtime/'chunk0.rpkg'
            # The same known v2 layout as the writer, without a patch deletion list.
            import struct
            payload = bytes(range(256))*3
            h = '0100000000000001'
            header = b'2KPR'+bytes([1,0,0,0,0,0,0,0x78,0x78])+struct.pack('<III',1,20,20)
            archive.write_bytes(header+struct.pack('<QQI',int(h,16),65,0)+b'EGLD'+struct.pack('<IIII',0,len(payload),3,4)+payload)
            definition = pkgdef_codec.encrypt(b'0123456789ABCDEF', b'@partition name=test patchlevel=2\n')
            (runtime/'packagedefinition.txt').write_bytes(definition)
            # An unrelated existing patch file must neither be overwritten nor removed.
            (runtime/'chunk0patch2.rpkg').write_bytes(b'other mod bytes')
            final = payload[:30]+b'Polska zmiana'+payload[40:]
            delta = make_delta(payload, final)
            item = {'hash':h,'type':'DLGE','archive':'chunk0.rpkg','path':f'resources/{h}.delta',
                    'original_payload_sha256':sha256(payload),'content_sha256':sha256(delta),'final_sha256':sha256(final)}
            package = root/'mod-data.zip'
            with zipfile.ZipFile(package,'w') as output:
                output.writestr('release.json',json.dumps({'format':1,'version':'test','partitions':{'0':'test'},'base_archive_fingerprints':fingerprints(game),'resources':[item]}))
                output.writestr(item['path'],delta)
            stage = root/'stage'
            with redirect_stdout(io.StringIO()):
                bundle = build_recipient_bundle(package, game, stage)
            self.assertTrue((stage/'chunk0patch3.rpkg').is_file())
            with patch('dub007.installer.closed_game'):
                result = install(bundle, game_root=game, backup_root=root/'backups')
                self.assertEqual(result['status'], 'installed')
                rollback(result['install_id'], backup_root=root/'backups', expected_game_root=game)
            self.assertEqual((runtime/'chunk0patch2.rpkg').read_bytes(), b'other mod bytes')
            self.assertFalse((runtime/'chunk0patch3.rpkg').exists())
            # Existing unrelated patch is kept active by the shared rollback policy.
            self.assertIn(b'patchlevel=2', checked_definition((runtime/'packagedefinition.txt').read_bytes())[2])
            self.assertEqual(archive.read_bytes()[-len(payload):], payload)


if __name__ == '__main__':
    unittest.main()
