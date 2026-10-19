from array import array
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dub007.audio import verify_original_spans, write_wav


class PreservedAudioTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        patcher = patch('dub007.audio.ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.original = array('h', range(1000))
        write_wav(self.root / 'original.wav', 1000, self.original)
        self.prepared = self.root / 'prepared.wav'
        self.row = {'sample_rate':1000, 'original_wav_path':'original.wav'}

    def test_prefix_is_identical_while_polish_speech_may_change(self):
        final = self.original[:200] + array('h', [-500] * 800)
        write_wav(self.prepared, 1000, final)
        self.row['preserve_original_prefix_until'] = .2
        result = verify_original_spans(self.row, self.prepared)
        self.assertEqual(result['preserved_sample_ranges'], [[0, 200]])
        final[199] += 1
        write_wav(self.prepared, 1000, final)
        with self.assertRaisesRegex(ValueError, 'PCM changed'):
            verify_original_spans(self.row, self.prepared)

    def test_foreign_suffix_is_checked_after_gain_and_segment_fitting(self):
        final = array('h', [-500] * 500) + self.original[500:]
        write_wav(self.prepared, 1000, final)
        self.row.update(segments_pl=[{'timing_raw':'0,.5'},{'timing_raw':'.5,1'}],
                        preserve_original_segment_indices=[1])
        self.assertEqual(verify_original_spans(self.row, self.prepared)['preserved_sample_ranges'], [[500, 1000]])
        final[999] += 1
        write_wav(self.prepared, 1000, final)
        with self.assertRaisesRegex(ValueError, 'PCM changed'):
            verify_original_spans(self.row, self.prepared)

    def test_invalid_selection_or_sample_rate_blocks_encoding(self):
        write_wav(self.prepared, 1000, self.original)
        self.row.update(segments_pl=[{'timing_raw':'0,1'}],preserve_original_segment_indices=[1])
        with self.assertRaisesRegex(ValueError, 'segment index'):
            verify_original_spans(self.row, self.prepared)
        self.row['preserve_original_segment_indices'] = [0]
        write_wav(self.prepared, 2000, self.original)
        with self.assertRaisesRegex(ValueError, 'sample rate'):
            verify_original_spans(self.row, self.prepared)

    def test_regular_polish_line_requires_no_original_file_access(self):
        self.assertIsNone(verify_original_spans({}, Path('missing.wav')))
