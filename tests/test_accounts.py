import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dub007.common import api_key, active_account, save_json
from dub007.ledger import CampaignLedger
from dub007.elevenlabs_api import included_subscription, verify_second_account
from dub007.billing import reconcile


class AccountTests(unittest.TestCase):
    def test_different_keys_for_same_account_do_not_create_a_second_allowance(self):
        with patch('dub007.elevenlabs_api.api_key', return_value='first-key'), \
             patch('dub007.elevenlabs_api.get', side_effect=[{'user_id': 'same'}, {'user_id': 'same'}]):
            with self.assertRaisesRegex(RuntimeError, 'same ElevenLabs account'):
                verify_second_account('second-key')
        with patch('dub007.elevenlabs_api.api_key', return_value='first-key'), \
             patch('dub007.elevenlabs_api.get', side_effect=[{'user_id': 'first'}, {'user_id': 'second'}]):
            verify_second_account('second-key')

    def test_selected_second_key_never_falls_back_to_first(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {}, clear=True):
            root = Path(directory)
            (root / '.env').write_text('ELEVENLABS_API_KEY=first\nDUB007_ELEVENLABS_ACCOUNT=2\n', encoding='utf-8')
            with patch('dub007.common.ROOT', root):
                self.assertEqual(active_account(), '2')
                self.assertEqual(api_key(), '')
                self.assertEqual(api_key('1'), 'first')
                with (root / '.env').open('a') as stream:
                    stream.write('ELEVENLABS_API_KEY_2=second\n')
                self.assertEqual(api_key(), 'second')

    def test_legacy_requests_keep_first_account_and_global_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = {'working_value_limit_pln': 1, 'pricing_usd_per_1000': .08,
                      'usd_pln_mid': 4, 'tax_assumed': .23}
            save_json(root / 'config/campaign.json', policy)
            path = root / 'ledger.sqlite3'
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE entries(id INTEGER PRIMARY KEY,audio_hash TEXT,cache_key TEXT,characters INTEGER,status TEXT,document TEXT,reserved_credits INTEGER,actual_credits INTEGER)')
            entry = {'audio_hash_en': 'old', 'cache_key': 'old', 'characters': 2000,
                     'status': 'succeeded', 'identity': {'request': {'text': 'a' * 2000}}}
            db.execute('INSERT INTO entries VALUES(1,?,?,?,?,?,?,?)',
                       ('old', 'old', 2000, 'succeeded', json.dumps(entry), 2000, 2000))
            db.commit(); db.close()
            with patch('dub007.ledger.ROOT', root), patch('dub007.ledger.active_account', return_value='2'):
                store = CampaignLedger(path)
                self.assertEqual(store.quota_credits('1'), 2000)
                self.assertEqual(store.quota_credits('2'), 0)
                with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                    store.reserve({'audio_hash_en': 'new', 'request': {'text': 'b' * 600}})
                second = store.reserve({'audio_hash_en': 'new', 'request': {'text': 'b' * 100}})
                self.assertEqual(second['account_id'], '2')
                self.assertEqual(store.quota_credits('2'), 100)
                second.update(status='succeeded', request_id='two')
                store.write({'entries': [second]})
                with patch('dub007.billing.api_key', side_effect=lambda account: 'key-' + account), \
                     patch('dub007.billing.get', return_value={'history': [{'request_id': 'two',
                           'character_count_change_from': 50, 'character_count_change_to': 150}]}) as history:
                    self.assertEqual(reconcile(store)['pending'], 0)
                    self.assertEqual(history.call_args.args[1], 'key-2')
                self.assertEqual(store.budget_credits(), 2100)
                store.close()

    def test_allowance_snapshot_is_scoped_to_account_and_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            class Store:
                policy = {}
                def quota_credits(self, account):
                    return 299998 if account == '1' else 50
            with patch('dub007.elevenlabs_api.ARTIFACTS', root), \
                 patch('dub007.elevenlabs_api.active_account', return_value='2'), \
                 patch('dub007.elevenlabs_api.get', return_value={'character_count': 100, 'character_limit': 10000}) as get:
                self.assertEqual(included_subscription('second', Store())['character_count'], 100)
                self.assertEqual(included_subscription('second', Store())['character_count'], 100)
                self.assertEqual(get.call_count, 1)
                included_subscription('rotated-second', Store())
                self.assertEqual(get.call_count, 2)
                self.assertTrue((root / 'campaign/account-2-credit-snapshot.json').exists())

    def test_upgrade_refresh_counts_only_post_upgrade_local_credits(self):
        with tempfile.TemporaryDirectory() as directory:
            class Store:
                policy = {'account_credit_epochs': {'1': {'ledger_baseline_credits': 300000}}}
                def quota_credits(self, account):
                    return 300100
            with patch('dub007.elevenlabs_api.ARTIFACTS', Path(directory)), \
                 patch('dub007.elevenlabs_api.active_account', return_value='1'), \
                 patch('dub007.elevenlabs_api.get', return_value={'character_count': 100, 'character_limit': 600000}):
                self.assertEqual(included_subscription('same-account-key', Store())['character_count'], 100)

    def test_new_credit_allowance_is_durable_and_includes_reservations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_json(root / 'config/campaign.json', {'working_value_limit_pln': 0,
                'pricing_usd_per_1000': .08, 'usd_pln_mid': 4, 'tax_assumed': .23,
                'authorized_credit_session': {'ledger_baseline_credits': 0, 'max_additional_credits': 100}})
            with patch('dub007.ledger.ROOT', root), patch('dub007.ledger.ARTIFACTS', root / 'artifacts'):
                path = root / 'ledger.sqlite3'
                store = CampaignLedger(path)
                entry = store.reserve({'audio_hash_en': 'one', 'request': {'text': 'a' * 80}})
                entry['status'] = 'succeeded'; store.write({'entries': [entry]}); store.close()
                store = CampaignLedger(path)
                self.assertEqual(store.usage()['new_credit_session']['accounted_credits'], 80)
                self.assertEqual(store.usage()['new_credit_session']['remaining_credits'], 20)
                with self.assertRaisesRegex(RuntimeError, 'additional credit budget'):
                    store.reserve({'audio_hash_en': 'two', 'request': {'text': 'b' * 21}})
                self.assertEqual(store.quota_credits(), 80)
                store.close()

    def test_second_account_estimate_does_not_show_first_accounts_package(self):
        from dub007.estimate import run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_json(root / 'artifacts/elevenlabs_preflight.json', {'account': {'character_limit': 300000}})
            save_json(root / 'artifacts/campaign/account-credit-snapshot.json', {'account': {'character_limit': 300000}})
            with patch('dub007.estimate.ROOT', root), patch('dub007.estimate.ARTIFACTS', root / 'artifacts'), \
                 patch('dub007.estimate.active_account', return_value='2'), \
                 patch('dub007.tts.usage', return_value={'submitted_characters': 0}):
                report = run()
            self.assertEqual(report['account_id'], '2')
            self.assertFalse(report['account_plan_checked'])
            self.assertNotIn('account_observed', report)
