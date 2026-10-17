import unittest
from unittest.mock import patch

from dub007.tts import generate, validate_language


class LanguageGuardTests(unittest.TestCase):
    def test_serbian_reaction_stops_before_credentials_or_paid_api(self):
        row = {"text_en": "[In Serbian] Yeah, yeah.", "text_pl_tts": "[snappy] Dobra, dobra."}
        with patch("dub007.tts.api_key") as key:
            with self.assertRaisesRegex(ValueError, "before any paid request"):
                generate(row)
            key.assert_not_called()

    def test_source_only_annotation_also_requires_review(self):
        with self.assertRaisesRegex(ValueError, "original-audio review"):
            validate_language({"text_en": "Calm down.", "text_source_script": "[In Serbian] Calm down."})

    def test_reviewed_mixed_lines_keep_foreign_segment_and_allow_english(self):
        row = {"text_en": "[Speaking in Hindi] For the rest of his life.",
               "segments_pl": [{"text": "[w hindi]"}, {"text": "Do końca jego życia."}],
               "preserve_original_segment_indices": [0]}
        validate_language(row)
        for indices in ([0, 1], [2], [-1]):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                validate_language({**row, "preserve_original_segment_indices": indices})

    def test_mentions_accessibility_and_english_annotations_are_not_foreign(self):
        for text in ("You speak Serbian?", "Serbian, you weren't kidding.",
                     "[Earpiece chatter]", "[sighs] Here we go.", "[In English] Go!"):
            with self.subTest(text=text):
                validate_language({"text_en": text})
