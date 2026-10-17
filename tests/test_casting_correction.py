import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dub007.common import save_json, sha256
from dub007.ledger import CampaignLedger
from dub007.tts import validate_casting


class CastingCorrectionTests(unittest.TestCase):
    def test_native_actor_code_uses_actual_voice_metadata_not_stale_display_name(self):
        voices = [{'voice_id': 'male-id', 'labels': {'gender': 'male'}}]
        validate_casting('STAFF_CIVUKM11', {'voice_id': 'male-id', 'voice_name': 'Sarah'}, voices)
        with self.assertRaisesRegex(ValueError, 'no paid request'):
            validate_casting('STAFF_CIVUKF11', {'voice_id': 'male-id', 'voice_name': 'Laura'}, voices)

    def test_canonical_named_role_gender_blocks_wrong_voice(self):
        voices = [{'voice_id': 'wrong', 'labels': {'gender': 'male'}},
                  {'voice_id': 'correct', 'labels': {'gender': 'female'}}]
        with self.assertRaisesRegex(ValueError, 'no paid request'):
            validate_casting('RONSON', {'voice_id': 'wrong', 'expected_gender': 'female'}, voices)
        validate_casting('RONSON', {'voice_id': 'correct', 'expected_gender': 'female'}, voices)

    def test_one_proven_correct_voice_take_preserves_history_and_blocks_further_takes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            evidence = b'Canonical local dialogue proves wrong casting.'
            (root / 'evidence.json').write_bytes(evidence)
            policy = {'working_value_limit_pln': 100, 'pricing_usd_per_1000': .08,
                      'usd_pln_mid': 4, 'tax_assumed': .23, 'max_versions_per_resource': 3}
            save_json(root / 'config/campaign.json', policy)
            with patch('dub007.ledger.ROOT', root), patch('dub007.ledger.ARTIFACTS', root / 'artifacts'):
                store = CampaignLedger(root / 'ledger.sqlite3')
                try:
                    wrong = {'audio_hash_en': 'line', 'voice_id': 'wrong', 'request': {'text': 'a'}}
                    for _ in range(3):
                        entry = store.reserve(wrong)
                        entry['status'] = 'succeeded'
                        store.write({'entries': [entry]})
                    corrected = {**wrong, 'voice_id': 'correct'}
                    with self.assertRaisesRegex(ValueError, 'retake limit'):
                        store.reserve(corrected)
                    policy['reviewed_casting_corrections'] = {'line': {
                        'one_corrected_take_only': True, 'prior_versions': 3,
                        'wrong_voice_id': 'wrong', 'correct_voice_id': 'correct',
                        'evidence_path': 'evidence.json', 'evidence_sha256': sha256(evidence)}}
                    save_json(root / 'config/campaign.json', policy)
                    with self.assertRaisesRegex(ValueError, 'retake limit'):
                        store.reserve(wrong)
                    (root / 'evidence.json').write_bytes(b'changed')
                    with self.assertRaisesRegex(ValueError, 'retake limit'):
                        store.reserve(corrected)
                    (root / 'evidence.json').write_bytes(evidence)
                    entry = store.reserve(corrected)
                    entry['status'] = 'succeeded'
                    store.write({'entries': [entry]})
                    self.assertEqual(store.budget_credits(), 4)
                    self.assertEqual(len(store.load('line')['entries']), 4)
                    with self.assertRaisesRegex(ValueError, 'retake limit'):
                        store.reserve(corrected)
                    policy['reviewed_casting_corrections']['line']['corrected_versions_maximum'] = 2
                    save_json(root / 'config/campaign.json', policy)
                    with self.assertRaisesRegex(ValueError, 'retake limit'):
                        store.reserve(wrong)
                    entry = store.reserve(corrected)
                    entry['status'] = 'succeeded'
                    store.write({'entries': [entry]})
                    self.assertEqual(store.budget_credits(), 5)
                    self.assertEqual(len(store.load('line')['entries']), 5)
                    with self.assertRaisesRegex(ValueError, 'retake limit'):
                        store.reserve(corrected)
                finally:
                    store.close()
