from array import array
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dub007.surround import original_profile, project_channels


class SurroundTests(unittest.TestCase):
    def test_dual_mono_stereo_retains_both_channels_and_frames(self):
        mono = array('h', [1000, -2000, 3000, 0])
        result = project_channels(mono, [1, 1], [0, 0])
        self.assertEqual(list(result[::2]), list(mono))
        self.assertEqual(list(result[1::2]), list(mono))
        with self.assertRaisesRegex(ValueError, 'Invalid reviewed'):
            project_channels(mono, [1, 1, 1], [0, 0, 0])

    def test_projection_retains_frames_channel_energy_delays_and_silent_lfe(self):
        mono = array('h', [1000, -2000, 4000] + [0] * 12)
        gains, delays = [1, .5, .25, 0, .75, .5], [0, 1, 2, 0, 3, 4]
        result = project_channels(mono, gains, delays)
        self.assertEqual(len(result), len(mono) * 6)
        self.assertFalse(any(result[3::6]))
        for index in (0, 1, 2, 4, 5):
            channel = result[index::6]
            self.assertFalse(any(channel[:delays[index]]))
            self.assertEqual(list(channel[delays[index]:delays[index] + 3]),
                             [round(value * gains[index]) for value in mono[:3]])

    def test_projection_refuses_to_discard_audible_tail_or_invent_lfe(self):
        with self.assertRaisesRegex(ValueError, 'audible speech tail'):
            project_channels(array('h', [1000] * 20), [1, 1, 1, 0, 1, 1], [0, 1, 0, 0, 0, 0])
        with self.assertRaisesRegex(ValueError, 'Invalid reviewed PA projection'):
            project_channels(array('h', [0] * 20), [1] * 6, [0] * 6)

    def test_unknown_multichannel_resource_stops_before_source_or_paid_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            (root / 'config/surround.json').write_text(json.dumps({'resources': {}}))
            with patch('dub007.surround.ROOT', root):
                with self.assertRaisesRegex(ValueError, 'review before TTS'):
                    original_profile({'audio_hash_en': 'unknown', 'speaker_id': 'PA'}, root / 'missing.wav')
