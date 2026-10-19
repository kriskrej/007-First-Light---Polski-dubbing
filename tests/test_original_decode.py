import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from dub007.campaign import original_wav
from dub007.common import sha256


def write_wav(path, count):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 48000, count, 'NONE', 'not compressed'))
        stream.writeframes(b'\x01\x00' * count)


class OriginalDecodeTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'source.wem').write_bytes(b'verified looped source')
        return {'audio_hash_en': 'one', 'channels': 1, 'sample_rate': 48000,
                'sample_count': 100, 'original_wem_path': 'source.wem',
                'original_payload_sha256': sha256((root / 'source.wem').read_bytes()),
                'original_markers': {'smpl': {'raw_hex': 'unchanged'}}}

    def test_loop_expanded_cache_is_rebuilt_once_without_changing_loop_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); row = self.fixture(root)
            markers = copy.deepcopy(row['original_markers'])
            cached = root / 'campaign/originals/one.wav'; write_wav(cached, 600)
            def decode(args, **kwargs):
                self.assertIn('-i', args)
                write_wav(Path(args[args.index('-o') + 1]), 100)
            with patch('dub007.campaign.ROOT', root), patch('dub007.campaign.ARTIFACTS', root), \
                    patch('dub007.campaign.subprocess.run', side_effect=decode) as decoder:
                original_wav(row); original_wav(row)
                self.assertEqual(decoder.call_count, 1)
            with wave.open(str(cached), 'rb') as stream:
                self.assertEqual(stream.getnframes(), 100)
            self.assertEqual(row['original_markers'], markers)

    def test_wrong_fresh_profile_is_rejected_and_existing_cache_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); row = self.fixture(root)
            cached = root / 'campaign/originals/one.wav'; write_wav(cached, 600)
            previous = cached.read_bytes()
            def decode(args, **kwargs):
                write_wav(Path(args[args.index('-o') + 1]), 99)
            with patch('dub007.campaign.ROOT', root), patch('dub007.campaign.ARTIFACTS', root), \
                    patch('dub007.campaign.subprocess.run', side_effect=decode):
                with self.assertRaisesRegex(ValueError, 'Original decoder profile mismatch for one'):
                    original_wav(row)
            self.assertEqual(cached.read_bytes(), previous)

    def test_changed_source_is_rejected_before_rebuilding_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); row = self.fixture(root)
            (root / 'source.wem').write_bytes(b'changed')
            with patch('dub007.campaign.ROOT', root), patch('dub007.campaign.ARTIFACTS', root), \
                    patch('dub007.campaign.subprocess.run') as decoder:
                with self.assertRaisesRegex(ValueError, 'Original WEM checksum changed'):
                    original_wav(row)
                decoder.assert_not_called()
