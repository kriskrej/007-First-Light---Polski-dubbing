from array import array
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dub007.audio import read_wav, write_wav
from dub007.common import sha256
from dub007.timing import prepare_segments, prepare_campaign_segments


class TimingTests(unittest.TestCase):
    def test_pause_fit_preserves_existing_decimal_boundaries_for_subtitle_validation(self):
        from dub007.subtitles import replacement_text
        row = {'chapter_id': 'test', 'duration': 10,
               'original_markers': {'cue ': {'points': [{'sample_offset': 0}]}},
               'segments_pl': [{'text': 'A', 'timing_raw': '0,2.074294'},
                               {'text': 'B', 'timing_raw': '2.074294,5.289080'},
                               {'text': 'C', 'timing_raw': '5.289080,35.289080'}]}
        def fit(candidate, *args, **kwargs):
            if candidate.get('reviewed_subtitle_segments') is None:
                candidate['segment_timing_qa'] = [
                    {'segment': 0}, {'segment': 1, 'final_speech_end': 3},
                    {'segment': 2, 'source_active_duration': 2.5, 'capacity': 2.1}]
                raise ValueError('Segment 2 needs adaptation (tempo 1.2)')
            return Path('prepared.wav')
        original = ''.join('//(' + segment['timing_raw'] + ')' + chr(92) * 2 + segment['text']
                           for segment in row['segments_pl'])
        with patch('dub007.timing.prepare_segments', side_effect=fit):
            prepare_campaign_segments(row, Path('unused.wav'))
        self.assertEqual(row['segments_pl'][1]['timing_raw'].split(',')[0], '2.074294')
        self.assertTrue(replacement_text(row, original, ['A', 'B', 'C']))

    def test_campaign_uses_only_a_bounded_existing_pause_for_a_longer_polish_word(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rate = 1000
            write_wav(root / 'original.wav', rate, array('h', [1000] * 2000))
            write_wav(root / 'take.wav', rate, array('h', [1000] * 2000))
            row = {'sample_rate': rate, 'sample_count': 2000, 'original_wav_path': 'original.wav',
                   'alignment_path': 'unused.json', 'original_rms_dbfs': -30,
                   'chapter_id': 'test', 'original_markers': {'cue ': {'points': [{'sample_offset': 0}]}},
                   'segments_pl': [{'text': 'A', 'timing_raw': '0,1'}, {'text': 'B', 'timing_raw': '1.4,3'}]}
            source = array('h', [1000] * 2000)
            with patch('dub007.timing.ROOT', root), \
                 patch('dub007.timing.load_take', return_value=(source, 'AB', [0, .5], [.2, 1.16])), \
                 patch('dub007.timing.subprocess.run') as convert:
                def stretch(arguments, **kwargs):
                    # Deterministic duration correction; no speech samples removed.
                    rate_in, speech = read_wav(Path(arguments[arguments.index('-i') + 1]))
                    factor = float(arguments[arguments.index('-af') + 1].split('=')[1])
                    write_wav(Path(arguments[-1]), rate_in, array('h', [1000] * round(len(speech) / factor)))
                convert.side_effect = stretch
                prepared = prepare_campaign_segments(row, root / 'take.wav')
            self.assertEqual(len(read_wav(prepared)[1]), 2000)
            self.assertLessEqual(row['pause_fit_review']['earlier_seconds'], .2)
            self.assertLess(row['segment_timing_qa'][0]['final_speech_end'],
                            row['segment_timing_qa'][1]['target_start'])
            self.assertLessEqual(row['timing_qa']['tempo'], 1.15)
            self.assertEqual(row['original_segments_pl'][1]['timing_raw'], '1.4,3')
            self.assertNotIn('reviewed_duration_extension_seconds', row)

    def test_campaign_does_not_shift_shared_variant_subtitles_automatically(self):
        row = {'chapter_id': 'test', 'audio_variant_count': 3,
               'original_markers': {'cue ': {'points': [{'sample_offset': 0}]}},
               'segment_timing_qa': [{'segment': 0}, {'segment': 1}]}
        with patch('dub007.timing.prepare_segments', side_effect=ValueError('Segment 1 needs adaptation (tempo 1.2)')) as fit:
            with self.assertRaises(ValueError):
                prepare_campaign_segments(row, Path('unused.wav'))
        self.assertEqual(fit.call_count, 1)

    def test_retake_reuses_matching_short_segment_and_records_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rate = 24000
            (root / 'old').mkdir()
            original = array('h', [1000] * (rate * 2))
            write_wav(root / 'original.wav', rate, original)
            current = array('h', [0] * (rate * 2))
            current[:rate] = array('h', [1000] * rate)
            current[rate:rate + 4800] = array('h', [1000] * 4800)
            old = array('h', [0] * (rate * 2))
            old[2400:7200] = array('h', [1000] * 4800)
            old[rate:rate + 4800] = array('h', [1000] * 4800)
            write_wav(root / 'current.wav', rate, current)
            write_wav(root / 'old/take.wav', rate, old)
            def alignment(a_end, b_end):
                return {'alignment': {'characters': list('A. B.'),
                    'character_start_times_seconds': [0, a_end, a_end, 1, b_end],
                    'character_end_times_seconds': [a_end, a_end, 1, b_end, b_end]}}
            (root / 'current.json').write_text(json.dumps(alignment(1, 1.2)))
            (root / 'old/alignment.json').write_text(json.dumps(alignment(.3, 1.2)))
            row = {'sample_rate': rate, 'sample_count': rate * 2, 'original_wav_path': 'original.wav',
                   'alignment_path': 'current.json', 'original_rms_dbfs': -25, 'request_id': 'current',
                   'segments_pl': [{'text': 'A.', 'timing_raw': '0,.5'}, {'text': 'B.', 'timing_raw': '1,2'}]}
            previous = {'audio_path': 'old/take.wav', 'alignment_path': 'old/alignment.json',
                        'audio_sha256': sha256((root / 'old/take.wav').read_bytes()), 'request_id': 'previous'}
            from dub007.campaign import compatible_retake
            previous['identity'] = {'voice_id': 'same-actor', 'direction': 'same character',
                'time_profile': {'sample_count': rate * 2}, 'pronunciation': '[snappy] A. B.',
                'request': {'text': '[snappy] A. B.', 'model_id': 'same-model'}}
            current_entry = {'identity': {**previous['identity'],
                'request': {'text': '[angry] A. B.', 'model_id': 'same-model'}}}
            # A quick neutral take must not silently rescue a longer angry one.
            with patch('dub007.timing.ROOT', root):
                with self.assertRaises(ValueError):
                    prepare_segments(row, root / 'current.wav', cached_takes=[
                        take for take in [previous] if compatible_retake(take, current_entry)])
            current_entry['identity']['request']['text'] = '[snappy] A. Revised B.'
            with patch('dub007.timing.ROOT', root):
                prepared = prepare_segments(row, root / 'current.wav', cached_takes=[
                    take for take in [previous] if compatible_retake(take, current_entry)])
            self.assertEqual(len(read_wav(prepared)[1]), rate * 2)
            self.assertEqual(row['segment_timing_qa'][0]['source_request_id'], 'previous')
            self.assertEqual(row['segment_timing_qa'][1]['source_request_id'], 'current')

    def test_mixed_language_prefix_is_preserved_and_polish_starts_in_its_window(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rate = 24000
            original = array("h", (round(2000 * math.sin(i * 2 * math.pi * 300 / rate)) if i < rate else 0 for i in range(rate * 2)))
            generated = array("h", (round(1800 * math.sin(i * 2 * math.pi * 500 / rate)) if 2400 <= i < 9600 else 0 for i in range(12000)))
            write_wav(root / "original.wav", rate, original)
            write_wav(root / "generated.wav", rate, generated)
            (root / "alignment.json").write_text(json.dumps({"alignment": {"characters": list("Tak."),
                "character_start_times_seconds": [0.1, 0.2, 0.3, 0.4], "character_end_times_seconds": [0.2, 0.3, 0.4, 0.4]}}))
            row = {"sample_rate": rate, "sample_count": rate * 2, "original_wav_path": "original.wav",
                   "alignment_path": "alignment.json", "original_rms_dbfs": -25,
                   "segments_pl": [{"text": "Foreign acknowledgment", "timing_raw": "0,1"}, {"text": "Tak.", "timing_raw": "1,2"}],
                   "preserve_original_segment_indices": [0]}
            with patch("dub007.timing.ROOT", root):
                prepared = prepare_segments(row, root / "generated.wav")
            _, final = read_wav(prepared)
            self.assertEqual(final[:rate], original[:rate])
            self.assertEqual(len(final), rate * 2)
            self.assertGreater(max(map(abs, final[rate:])), 0)
            self.assertTrue(row["segment_timing_qa"][0]["original_segment_preserved"])
            self.assertEqual(row["segment_timing_qa"][1]["target_start"], 1)


if __name__ == "__main__":
    unittest.main()
