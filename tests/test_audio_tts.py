import tempfile
from pathlib import Path
import unittest
import wave

from dub007.encoding import restore_markers
from dub007.selection import riff_metadata, native_labels
from dub007.tts import pcm_to_wav, reserve, recover
from unittest.mock import patch
import time
import struct
from array import array
import shutil

from dub007.audio import prepare, prepare_campaign, read_wav, write_wav, target_samples


class AudioTtsTests(unittest.TestCase):
    def test_campaign_uses_existing_quiet_tail_without_new_frames_or_cut_speech(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original, generated = folder / 'original.wav', folder / 'raw.wav'
            write_wav(original, 1000, array('h', [0] * 200 + [1000] * 1000 + [0] * 800))
            write_wav(generated, 1000, array('h', [0] * 100 + [1000] * 1600 + [0] * 300))
            row = {'sample_rate': 1000, 'channels': 1, 'sample_count': 2000,
                   'original_wav_path': str(original), 'original_rms_dbfs': -30,
                   'scene_line_number': 1, 'chapter_id': 'test', 'segments_pl': [{}],
                   'available_tail_extension_seconds': .25,
                   'original_markers': {'cue ': {'points': [{'sample_offset': 0}]}}}
            def resample(arguments, **kwargs):
                shutil.copyfile(generated, arguments[-1])
            with patch('dub007.audio.ffmpeg', return_value='mock-ffmpeg'), \
                 patch('dub007.audio.subprocess.run', side_effect=resample):
                prepared = prepare_campaign(row, generated)
            rate, samples = read_wav(prepared)
            self.assertEqual((rate, len(samples)), (1000, 2000))
            self.assertEqual(sum(value != 0 for value in samples), 1600)
            self.assertLessEqual(row['timing_qa']['original_tail_used_seconds'], .5)
            self.assertTrue(row['quiet_fit_review']['original_frame_count_retained'])
            self.assertEqual(row['quiet_fit_review']['new_paid_requests'], 0)

    def test_campaign_quiet_retry_keeps_all_speech_and_original_frame_count(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original, generated = folder / 'original.wav', folder / 'raw.wav'
            write_wav(original, 1000, array('h', [0] * 200 + [1000] * 200 + [0] * 200))
            write_wav(generated, 1000, array('h', [0] * 100 + [1000] * 430 + [0] * 170))
            row = {'sample_rate': 1000, 'channels': 1, 'sample_count': 600,
                   'original_wav_path': str(original), 'original_rms_dbfs': -30,
                   'scene_line_number': 1, 'chapter_id': 'test', 'segments_pl': [{}],
                   'available_tail_extension_seconds': .25,
                   'original_markers': {'cue ': {'points': [{'sample_offset': 0}]}}}
            def resample(arguments, **kwargs):
                shutil.copyfile(generated, arguments[-1])
            with patch('dub007.audio.ffmpeg', return_value='mock-ffmpeg'), \
                 patch('dub007.audio.subprocess.run', side_effect=resample):
                prepared = prepare_campaign(row, generated)
            rate, samples = read_wav(prepared)
            self.assertEqual((rate, len(samples)), (1000, 600))
            self.assertEqual(sum(value != 0 for value in samples), 430)
            self.assertEqual(row['timing_qa']['tempo'], 1)
            self.assertLessEqual(row['timing_qa']['onset_adjustment_seconds'], .1)
            self.assertNotIn('reviewed_duration_extension_seconds', row)
            self.assertEqual(row['quiet_fit_review']['new_paid_requests'], 0)

    def test_campaign_quiet_retry_does_not_move_internal_sync_cues(self):
        row = {'chapter_id': 'test', 'segments_pl': [{}],
               'original_markers': {'cue ': {'points': [{'sample_offset': 0}, {'sample_offset': 100}]}}}
        with patch('dub007.audio.prepare', side_effect=ValueError('Line 1 needs shorter text or retake')) as fit:
            with self.assertRaises(ValueError):
                prepare_campaign(row, Path('unused.wav'))
        self.assertEqual(fit.call_count, 1)

    def test_duration_extension_preserves_original_metadata_and_rejects_sync_cues(self):
        row = {'sample_count': 17613, 'sample_rate': 48000, 'chapter_id': 'out-of-the-ashes',
               'segments_pl': [{'timing_raw': '0,30'}], 'reviewed_duration_extension_seconds': .15,
               'duration_extension_reason': 'Short acknowledgement requires natural Polish delivery.',
               'original_markers': {'cue ': {'points': [{'sample_offset': 0}]}}}
        self.assertEqual(target_samples(row), 24813)
        self.assertEqual(row['sample_count'], 17613)
        row['original_markers']['cue ']['points'].append({'sample_offset': 100})
        with self.assertRaisesRegex(ValueError, 'reviewed'):
            target_samples(row)
        row['original_markers']['cue ']['points'].pop()
        row.pop('chapter_id')
        with self.assertRaisesRegex(ValueError, 'reviewed'):
            target_samples(row)

    def test_reviewed_padding_trim_removes_only_quiet_guard_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            original, generated = folder / 'original.wav', folder / 'raw.wav'
            write_wav(original, 1000, array('h', [0] * 100 + [1000] * 200 + [0] * 200))
            write_wav(generated, 1000, array('h', [0] * 200 + [1000] * 200 + [0] * 200))
            row = {'sample_rate': 1000, 'channels': 1, 'original_wav_path': str(original),
                   'sample_count': 500, 'original_rms_dbfs': -30, 'scene_line_number': 1,
                   'edge_padding_reduction_seconds': 0.015}
            def resample(arguments, **kwargs):
                shutil.copyfile(generated, arguments[-1])
            with patch('dub007.audio.ffmpeg', return_value='mock-ffmpeg'), patch('dub007.audio.subprocess.run', side_effect=resample):
                prepared = prepare(row, generated)
            rate, samples = read_wav(prepared)
            self.assertEqual((rate, len(samples)), (1000, 500))
            self.assertEqual(sum(value != 0 for value in samples), 200)
            self.assertEqual(row['timing_qa']['trimmed_leading_padding_seconds'], 0.015)
            self.assertEqual(row['timing_qa']['trimmed_trailing_padding_seconds'], 0.015)
            self.assertEqual(row['timing_qa']['tempo'], 1)

    def test_native_labels_respect_lengths_without_null_terminators(self):
        def label(cue, text, padding=False):
            body = struct.pack('<I', cue) + text.encode()
            return b'labl' + struct.pack('<I', len(body)) + body + (b'\0' if padding and len(body) % 2 else b'')
        raw = b'adtl' + label(0, 's13363_qminenag2_q_001') + label(1, 's13363_qminenag2_q_002', True)
        self.assertEqual(native_labels({'LIST': {'raw_hex': raw.hex()}}), [
            {'cue_id': 0, 'label': 's13363_qminenag2_q_001'},
            {'cue_id': 1, 'label': 's13363_qminenag2_q_002'}])
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            native_labels({'LIST': {'raw_hex': raw[:-3].hex()}})

    def identity(self, audio_hash="one", text="a"):
        return {"audio_hash_en": audio_hash, "request": {"text": text}}

    def test_character_limit_includes_all_reserved_submissions(self):
        ledger = {"entries": []}
        entry = reserve(ledger, self.identity(text="x" * 9999))
        entry["status"] = "http_rejected"
        with self.assertRaisesRegex(ValueError, "character limit"):
            reserve(ledger, self.identity("two", "[calm] ok"))

    def test_unknown_request_blocks_paid_repeat(self):
        ledger = {"entries": []}
        entry = reserve(ledger, self.identity())
        entry["status"] = "unknown"
        with self.assertRaisesRegex(RuntimeError, "Unresolved"):
            reserve(ledger, self.identity(text="changed text"))

    def test_ten_distinct_lines_and_one_retake(self):
        ledger = {"entries": []}
        for index in range(10):
            reserve(ledger, self.identity(str(index)))["status"] = "succeeded"
        with self.assertRaisesRegex(ValueError, "10 distinct"):
            reserve(ledger, self.identity("eleventh"))
        reserve(ledger, self.identity("0"))["status"] = "succeeded"
        with self.assertRaisesRegex(ValueError, "additional version"):
            reserve(ledger, self.identity("0"))

    def test_pcm_sync_failure_preserves_previous_cached_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'raw.wav'
            pcm_to_wav(b'\x01\x00' * 2400, output, 24000)
            previous = output.read_bytes()
            with patch('dub007.tts.os.fsync', side_effect=OSError('disk flush failed')):
                with self.assertRaisesRegex(OSError, 'disk flush failed'):
                    pcm_to_wav(b'\x02\x00' * 4800, output, 24000)
            self.assertEqual(output.read_bytes(), previous)
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_pcm_replace_failure_leaves_previous_valid_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'raw.wav'
            pcm_to_wav(b'\x01\x00' * 2400, output, 24000)
            previous = output.read_bytes()
            with patch('dub007.tts.os.replace', side_effect=PermissionError('file in use')):
                with self.assertRaisesRegex(PermissionError, 'file in use'):
                    pcm_to_wav(b'\x02\x00' * 4800, output, 24000)
            self.assertEqual(output.read_bytes(), previous)
            with wave.open(str(output), 'rb') as stream:
                self.assertEqual(stream.readframes(2400), b'\x01\x00' * 2400)
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_pcm_is_wrapped_as_real_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "raw.wav"
            pcm_to_wav(b"\x01\x00" * 2400, output, 24000)
            with wave.open(str(output), "rb") as stream:
                self.assertEqual((stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()), (1, 2, 24000, 2400))
            with self.assertRaises(ValueError):
                pcm_to_wav(b"odd", output, 24000)

    def test_marker_roundtrip_and_out_of_range_rejection(self):
        fmt = struct.pack("<HHIIHH", 1, 1, 48000, 96000, 2, 16)
        body = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 8) + b"\x00" * 8
        data = b"RIFF" + struct.pack("<I", len(body)) + body
        cue = struct.pack("<I", 1) + struct.pack("<II4sIII", 123, 0, b"data", 0, 0, 3)
        markers = {"cue ": {"raw_hex": cue.hex(), "points": [{"sample_offset": 3}]}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "test.wem"
            output.write_bytes(data)
            restore_markers(output, markers, 4)
            parsed = riff_metadata(output.read_bytes())
            self.assertEqual(parsed["original_markers"]["cue "]["points"][0]["id"], 123)
            self.assertEqual(parsed["original_markers"]["cue "]["points"][0]["sample_offset"], 3)
            # A streaming header reader may stop at data; markers must already
            # be visible, matching First Light's original WEM ordering.
            self.assertEqual([chunk["id"] for chunk in parsed["riff_chunks"]], ["fmt ", "cue ", "data"])
            audio = parsed["riff_chunks"][-1]
            self.assertEqual(output.read_bytes()[audio["offset"] + 8:], b"\x00" * 8)
            first = output.read_bytes()
            restore_markers(output, markers, 4)
            self.assertEqual(output.read_bytes(), first)
            with self.assertRaisesRegex(ValueError, "beyond"):
                restore_markers(output, markers, 2)

    def test_redacted_history_can_recover_by_saved_request_id(self):
        now = time.time()
        entry = {"request_id": "request-one", "started_unix": now,
                 "identity": {"voice_id": "voice-one", "request": {"text": "Test", "model_id": "eleven_v4", "voice_settings": {"stability": 0.5}}}}
        history = {"history": [{"request_id": "request-one", "history_item_id": "history-one", "text": None,
                               "voice_id": None, "model_id": "eleven_v4", "date_unix": int(now), "settings": {"stability": 0.5}}]}
        with patch("dub007.tts.get", return_value=history):
            self.assertEqual(len(recover(entry, "test-key")), 1)

    def test_redacted_history_without_request_id_does_not_guess(self):
        now = time.time()
        entry = {"started_unix": now,
                 "identity": {"voice_id": "voice-one", "request": {"text": "Test", "model_id": "eleven_v4", "voice_settings": {"stability": 0.5}}}}
        history = {"history": [{"request_id": "unknown-request", "text": None, "voice_id": None,
                               "model_id": "eleven_v4", "date_unix": int(now), "settings": {"stability": 0.5}}]}
        with patch("dub007.tts.get", return_value=history):
            self.assertEqual(recover(entry, "test-key"), [])

    def test_single_dialogue_history_matches_full_identity_without_voice_filter(self):
        now = time.time()
        entry = {"started_unix": now, "identity": {"voice_id": "voice-one", "request": {
            "text": "Drabiną w dół.", "model_id": "eleven_v4", "voice_settings": {"stability": 0.5}}}}
        item = {"request_id": "one", "text": None, "voice_id": None,
                "dialogue": [{"text": "Drabiną w dół.", "voice_id": "voice-one"}],
                "model_id": "eleven_v4", "date_unix": int(now), "settings": {"stability": 0.5}}
        with patch("dub007.tts.get", return_value={"history": [item]}) as get:
            self.assertEqual(recover(entry, "test-key"), [item])
            self.assertNotIn("voice_id=", get.call_args.args[0])

    def test_dialogue_recovery_rejects_mismatch_multiple_speakers_and_stale_settings(self):
        import copy
        now = time.time()
        entry = {"started_unix": now, "identity": {"voice_id": "voice-one", "request": {
            "text": "Test", "model_id": "eleven_v4", "voice_settings": {"stability": 0.5}}}}
        item = {"text": None, "voice_id": None, "dialogue": [{"text": "Test", "voice_id": "voice-one"}],
                "model_id": "eleven_v4", "date_unix": int(now), "settings": {"stability": 0.5}}
        variants = []
        for field, value in [('text', 'Other text'), ('voice_id', 'other-voice')]:
            changed = copy.deepcopy(item)
            changed['dialogue'][0][field] = value
            variants.append(changed)
        variants.append({**item, 'dialogue': item['dialogue'] * 2})
        variants.append({**item, 'date_unix': int(now) - 600})
        variants.append({**item, 'settings': {'stability': 0.7}})
        variants.append({**item, 'model_id': 'another-model'})
        with patch("dub007.tts.get", return_value={"history": variants}):
            self.assertEqual(recover(entry, "test-key"), [])

    def test_saved_request_id_never_falls_back_to_another_identical_take(self):
        now = time.time()
        entry = {"request_id": "expected", "started_unix": now,
                 "identity": {"voice_id": "voice-one", "request": {"text": "Test", "model_id": "eleven_v4", "voice_settings": {"stability": 0.5}}}}
        item = {"request_id": "different", "text": "Test", "voice_id": "voice-one",
                "model_id": "eleven_v4", "date_unix": int(now), "settings": {"stability": 0.5}}
        with patch("dub007.tts.get", return_value={"history": [item]}):
            self.assertEqual(recover(entry, "test-key"), [])

    def test_recovery_preserves_ambiguity_between_identical_adjacent_takes(self):
        now = time.time()
        entry = {"started_unix": now, "identity": {"voice_id": "voice-one", "request": {
            "text": "Test", "model_id": "eleven_v4", "voice_settings": {"stability": 0.5}}}}
        item = {"text": None, "voice_id": None, "dialogue": [{"text": "Test", "voice_id": "voice-one"}],
                "model_id": "eleven_v4", "date_unix": int(now), "settings": {"stability": 0.5}}
        with patch("dub007.tts.get", return_value={"history": [{**item, 'request_id': 'one'}, {**item, 'request_id': 'two'}]}):
            self.assertEqual(len(recover(entry, "test-key")), 2)


if __name__ == "__main__":
    unittest.main()
