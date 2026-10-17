import unittest
from unittest.mock import patch

from dub007.campaign import scene_key
from dub007.reactive import matches_scope


class ReactiveTests(unittest.TestCase):
    def test_reactive_scope_requires_both_paths_and_native_label_without_story_number(self):
        configuration = {
            "scope_kind": "reactive", "reactive_speaker": "BOND",
            "reactive_audio_prefix": "[assembly:/voices/ai_dialog/bond/",
            "reactive_dialogue_prefix": "[assembly:/conversations/ai_dialog/bond/",
            "reactive_location_tokens": ["pepper", "ivy"],
        }
        row = {
            "speaker_id": "BOND", "audio_hash_en": "one", "reactive_scope": "late",
            "audio_source_path": "[assembly:/voices/ai_dialog/bond/bluff_pepper_bond_bond_001.wav].wes",
            "dialogue_source_path": "[assembly:/conversations/ai_dialog/bond/bluff_pepper_bond.sweetdialog].dialogevent",
            "native_audio_label": "bluff_pepper_bond_bond_001",
        }
        config = {"chapters": {"late": configuration}}
        with patch("dub007.campaign.chapter_config", return_value=config):
            self.assertEqual(scene_key(row), (0, "bluff_pepper_bond", 1, "one"))
            for field, value in (
                ("native_audio_label", "other_bond_001"),
                ("dialogue_source_path", "[assembly:/conversations/ai_dialog/bond/bluff_clover.sweetdialog].dialogevent"),
                ("audio_source_path", "[assembly:/voices/ai_dialog/bond/bluff_ivyleaf_bond_001.wav].wes"),
                ("speaker_id", "Q"),
                ("reactive_scope", "unreviewed"),
            ):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    scene_key({**row, field: value})
        self.assertFalse(matches_scope(row, row["audio_source_path"], row["dialogue_source_path"], {}))
