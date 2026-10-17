import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from dub007.batch import run
from dub007.common import save_json
from dub007.ledger import CampaignLedger


class BatchTests(unittest.TestCase):
    def test_first_pass_never_retries_a_paid_timing_failure_or_foreign_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.sqlite3'
            def connect():
                return sqlite3.connect(path)
            with closing(connect()) as db, db:
                db.execute('CREATE TABLE resources (audio_hash TEXT PRIMARY KEY, document TEXT)')
                db.execute('CREATE TABLE chapter_resources (chapter TEXT, audio_hash TEXT, ordinal INTEGER)')
                for ordinal, (h, generation, qa) in enumerate([
                    ('fresh-one', 'not_started', 'original_metadata_verified'),
                    ('old-failure', 'generated', 'needs_agent_adaptation'),
                    ('foreign', 'not_started', 'preserved_foreign_language'),
                    ('fresh-two', 'not_started', 'original_metadata_verified'),
                ]):
                    db.execute('INSERT INTO resources VALUES (?,?)', (h, json.dumps({
                        'audio_hash_en': h, 'generation_status': generation, 'qa_status': qa})))
                    db.execute('INSERT INTO chapter_resources VALUES (?,?,?)', ('chapter', h, ordinal))
            class Store:
                def __init__(self):
                    self.database = connect()
                def close(self):
                    self.database.close()
            calls = []
            def generate(lines, chapter, audio_hashes):
                calls.append(audio_hashes)
                h = audio_hashes[0]
                failed = h == 'fresh-one'
                with closing(connect()) as db, db:
                    row = json.loads(db.execute('SELECT document FROM resources WHERE audio_hash=?', (h,)).fetchone()[0])
                    row.update(generation_status='generated', qa_status='needs_agent_adaptation' if failed else 'offline_final_wem_validated')
                    db.execute('UPDATE resources SET document=? WHERE audio_hash=?', (json.dumps(row), h))
                return {'needs_attention': {'audio_hash_en': h, 'qa_error': 'Timing fit failed'}} if failed else {}
            with patch('dub007.batch.database', side_effect=Store), \
                 patch('dub007.batch.ensure_chapter'), patch('dub007.batch.progress', return_value={}), \
                 patch('dub007.batch.generate', side_effect=generate), patch('builtins.print'):
                report = run('chapter')
            self.assertEqual(calls, [['fresh-one', 'fresh-two'], ['fresh-two']])
            self.assertEqual(report['remaining_adaptations'], 2)
            self.assertEqual(report['automatically_purchased_retakes'], 0)

    def test_diagnostic_hold_cannot_reserve_a_paid_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_json(root / 'config/campaign.json', {'generation_hold': {'reason': 'diagnostic test'}})
            with patch('dub007.ledger.ROOT', root), patch('dub007.ledger.ARTIFACTS', root / 'artifacts'):
                store = CampaignLedger(root / 'test.sqlite3')
                with self.assertRaisesRegex(RuntimeError, 'held for diagnostics'):
                    store.reserve({'audio_hash_en': 'one', 'request': {'text': 'test'}})
                self.assertEqual(store.database.execute('SELECT count(*) FROM entries').fetchone()[0], 0)
                store.close()
