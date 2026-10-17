import copy
import unittest

from dub007.pronunciation import apply, spoken_text


class PronunciationTests(unittest.TestCase):
    def test_agent_ids_keep_both_zeros_without_changing_other_numbers(self):
        self.assertEqual(spoken_text("001, 007 i 009. 10 000, 1007, A007, G9 i B4."),
                         "zero zero jeden, zero zero siedem i zero zero dziewięć. 10 000, 1007, A007, G9 i B4.")

    def test_agent_id_alignment_is_normalized_and_official_subtitles_are_preserved(self):
        row = {"text_pl_tts": "[snappy] Tu 007 z MI6. Szukam 009.",
               "text_pl_display": "Tu 007 z MI6. Szukam 009.",
               "segments_pl": [{"text": "Tu 007 z MI6."}, {"text": "Szukam 009."}]}
        original = copy.deepcopy(row)
        apply(row)
        self.assertEqual(row["text_pl_tts"], "[snappy] Tu zero zero siedem z Em Aj Six. Szukam zero zero dziewięć.")
        self.assertEqual(row["spoken_segments"], ["Tu zero zero siedem z Em Aj Six.", "Szukam zero zero dziewięć."])
        self.assertEqual(row["segments_pl"], original["segments_pl"])
        self.assertEqual(row["text_pl_display"], original["text_pl_display"])
        once = copy.deepcopy(row)
        apply(row)
        self.assertEqual(row, once)

    def test_mi6_variants_and_word_boundaries(self):
        self.assertEqual(spoken_text("MI6, mi6, MI 6, MI-6, em i sześć, Em I Sześć."),
                         "Em Aj Six, Em Aj Six, Em Aj Six, Em Aj Six, Em Aj Six, Em Aj Six.")
        self.assertEqual(spoken_text("M16 MI60 XMI6 MI6x Em Aj Six"),
                         "M16 MI60 XMI6 MI6x Em Aj Six")

    def test_alignment_matches_new_speech_but_subtitles_are_unchanged(self):
        row = {"text_pl_tts": "[snappy] Tu MI6. Zna em i sześć.",
               "text_pl_display": "Tu MI6. Zna MI6.",
               "segments_pl": [{"text": "Tu MI6."}, {"text": "Zna MI6."}],
               "spoken_segments": ["Tu MI6.", "Zna em i sześć."]}
        original = copy.deepcopy(row)
        apply(row)
        self.assertEqual(row["text_pl_tts"], "[snappy] Tu Em Aj Six. Zna Em Aj Six.")
        self.assertEqual(row["spoken_segments"], ["Tu Em Aj Six.", "Zna Em Aj Six."])
        for name in ("text_pl_display", "segments_pl"):
            self.assertEqual(row[name], original[name])
        once = copy.deepcopy(row)
        apply(row)
        self.assertEqual(row, once)

    def test_alignment_fallback_uses_display_parts_without_mutating_them(self):
        row = {"text_pl_tts": "Tu MI6.", "display_segments": ["Tu MI6."]}
        apply(row)
        self.assertEqual(row["spoken_segments"], ["Tu Em Aj Six."])
        self.assertEqual(row["display_segments"], ["Tu MI6."])

    def test_script_adaptation_refreshes_stale_mi6_segments_without_new_tts_text(self):
        row = {"text_pl_tts": "[snappy] Ładunki MI6. To wy prowadzicie akcję odzyskania.",
               "display_segments": ["Ładunki MI6.", "To wy prowadzicie akcję odzyskania."],
               "spoken_segments": ["Materiały wybuchowe Em Aj Six.", "To wy prowadzicie tę misję poszukiwawczą."]}
        apply(row)
        self.assertEqual(row["spoken_segments"], ["Ładunki Em Aj Six.", "To wy prowadzicie akcję odzyskania."])
        self.assertEqual(row["text_pl_tts"], "[snappy] Ładunki Em Aj Six. To wy prowadzicie akcję odzyskania.")
        once = copy.deepcopy(row)
        apply(row)
        self.assertEqual(row, once)

    def test_explicit_phonetic_segments_remain_and_invalid_script_stops_before_post(self):
        row = {"text_pl_tts": "[snappy] Doktor. Kanał trzy.",
               "display_segments": ["Dr.", "Kanał 3."],
               "spoken_segments": ["Doktor.", "Kanał trzy."]}
        apply(row)
        self.assertEqual(row["spoken_segments"], ["Doktor.", "Kanał trzy."])
        row["text_pl_tts"] = "Different text."
        with self.assertRaisesRegex(ValueError, "before any paid request"):
            apply(row)
