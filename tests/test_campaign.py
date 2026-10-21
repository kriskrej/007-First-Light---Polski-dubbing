import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dub007.campaign import scene_key, order_key, database, run
from dub007.ledger import CampaignLedger
from dub007.installer import plan, rollback
from dub007.common import save_json
from dub007.deployment import recover_pending
from dub007.billing import reconcile
import test_offline as fixtures


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.policy_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.policy_directory.cleanup)
        root = Path(self.policy_directory.name)
        save_json(root / 'config/campaign.json', {
            'working_value_limit_pln': 0, 'pricing_usd_per_1000': .08,
            'usd_pln_mid': 4, 'tax_assumed': .23, 'max_versions_per_resource': 3})
        for name, value in [('ROOT', root), ('ARTIFACTS', root / 'artifacts')]:
            patcher = patch('dub007.ledger.' + name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_policy_does_not_create_or_lock_a_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'ledger.sqlite3'
            with patch('dub007.ledger.ROOT', root):
                with self.assertRaises(FileNotFoundError):
                    CampaignLedger(path)
            self.assertFalse(path.exists())

    def test_scope_change_blocks_unlisted_audio_without_reservation(self):
        root = Path(self.policy_directory.name)
        path = root / 'config/campaign.json'
        policy = json.loads(path.read_text(encoding='utf-8'))
        policy.update(working_value_limit_pln=100, paid_audio_allowlist=['included'])
        save_json(path, policy)
        with closing(CampaignLedger()) as store:
            identity = {'audio_hash_en': 'included', 'request': {'text': 'Cześć.'}}
            store.reserve(identity)
            # An already-open worker must re-read the narrowed scope.
            policy['paid_audio_allowlist'] = []
            save_json(path, policy)
            for audio_hash in ('included', 'excluded'):
                with self.assertRaisesRegex(RuntimeError, 'authorized generation scope'):
                    store.reserve({**identity, 'audio_hash_en': audio_hash})
            self.assertEqual(store.usage()['requests'], 1)

    def test_invalid_generation_scope_never_becomes_unrestricted(self):
        root = Path(self.policy_directory.name)
        path = root / 'config/campaign.json'
        policy = json.loads(path.read_text(encoding='utf-8'))
        with closing(CampaignLedger()) as store:
            for invalid in ('included', False, [None]):
                policy['paid_audio_allowlist'] = invalid
                save_json(path, policy)
                with self.assertRaisesRegex(ValueError, 'allowlist'):
                    store.reserve({'audio_hash_en': 'included', 'request': {'text': 'Cześć.'}})
            self.assertEqual(store.usage()['requests'], 0)

    def test_one_explicit_model_correction_cannot_become_repeated_retakes(self):
        from dub007.common import sha256
        root = Path(self.policy_directory.name)
        path = root/'config/campaign.json'
        policy = json.loads(path.read_text(encoding='utf-8'))
        proof = root/'review.json'
        proof.write_text('finite reviewed model change', encoding='utf-8')
        policy.update(working_value_limit_pln=100, reviewed_model_timing_corrections={'one': {
            'prior_versions':3,'maximum_new_versions':1,'previous_model_id':'old','new_model_id':'new',
            'voice_id':'voice','reason':'Three known takes fail the duration; one distinct model only.',
            'evidence_path':'review.json','evidence_sha256':sha256(proof.read_bytes())}})
        save_json(path,policy)
        with closing(CampaignLedger()) as store:
            old={'audio_hash_en':'one','voice_id':'voice','request':{'text':'Hi','model_id':'old'}}
            for _ in range(3):
                entry=store.reserve(old)
                entry['status']='succeeded'
                store.write({'entries':[entry]})
            for wrong in ({**old,'voice_id':'other'}, old):
                self.assertFalse(store.reviewed_model_timing_correction(wrong))
            changed={**old,'request':{'text':'Hi','model_id':'new'}}
            self.assertTrue(store.reviewed_model_timing_correction(changed))
            proof.write_text('tampered review', encoding='utf-8')
            self.assertFalse(store.reviewed_model_timing_correction(changed))
            proof.write_text('finite reviewed model change', encoding='utf-8')
            entry=store.reserve(changed)
            entry['status']='succeeded'
            store.write({'entries':[entry]})
            with self.assertRaisesRegex(ValueError,'retake limit'):
                store.reserve(changed)

    def test_opening_complete_chapter_mapping_does_not_compete_with_another_writer(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder, closing(CampaignLedger(Path(folder) / 'ledger.sqlite3')) as store:
            path = store.path
            with patch('dub007.campaign.CampaignLedger', return_value=store):
                database()
            with store.database:
                store.database.execute('INSERT INTO resources VALUES(?,?,?,?,?,?)',
                                       ('one', 0, 'chapter', 'scene', 'ready', '{}'))
                store.database.execute('INSERT INTO chapter_resources VALUES(?,?,?)',
                                       ('chapter', 'one', 0))
            store.database.execute('PRAGMA busy_timeout=20')
            writer = sqlite3.connect(path)
            try:
                writer.execute('BEGIN IMMEDIATE')
                writer.execute("UPDATE resources SET scene='uncommitted' WHERE audio_hash='one'")
                with closing(CampaignLedger(path)) as independent:
                    self.assertEqual(independent.usage()['requests'], 0)
                with patch('dub007.campaign.CampaignLedger', return_value=store):
                    opened = database()
                self.assertIs(opened, store)
                self.assertEqual(store.database.execute('SELECT scene FROM resources').fetchone()[0], 'scene')
                self.assertEqual(store.database.execute('SELECT COUNT(*) FROM chapter_resources').fetchone()[0], 1)
            finally:
                writer.rollback()
                writer.close()

    def test_legacy_chapter_mapping_is_backfilled_without_replacing_shared_membership(self):
        with tempfile.TemporaryDirectory() as folder, closing(CampaignLedger(Path(folder) / 'ledger.sqlite3')) as store:
            with patch('dub007.campaign.CampaignLedger', return_value=store):
                database()
            with store.database:
                store.database.executemany('INSERT INTO resources VALUES(?,?,?,?,?,?)',
                                          [('one', 0, 'original', 's', 'ready', '{}'),
                                           ('two', 1, 'legacy', 's', 'ready', '{}')])
                store.database.executemany('INSERT INTO chapter_resources VALUES(?,?,?)',
                                          [('original', 'one', 7), ('shared', 'one', 3)])
            with patch('dub007.campaign.CampaignLedger', return_value=store):
                database()
                database()
            self.assertEqual(store.database.execute(
                'SELECT chapter,audio_hash,ordinal FROM chapter_resources ORDER BY chapter').fetchall(),
                [('legacy', 'two', 1), ('original', 'one', 7), ('shared', 'one', 3)])

    def test_import_resolves_scenes_before_taking_the_database_writer_lock(self):
        import sqlite3
        from dub007.campaign import ensure_chapter
        with tempfile.TemporaryDirectory() as folder, closing(CampaignLedger(Path(folder) / 'ledger.sqlite3')) as store:
            with patch('dub007.campaign.CampaignLedger', return_value=store):
                database()
            with store.database:
                store.database.execute('INSERT INTO state VALUES(?,?)', ('writer_probe', 'unchanged'))
            manifest = Path(folder) / 'manifest.json'
            rows = [{'audio_hash_en': name, 'speaker_id': 'speaker', 'text_en': 'Hello.',
                     'text_pl_original': 'Cześć.', 'qa_status': 'original_metadata_verified',
                     'original_payload_sha256': name, 'uses': [{'line_id': name, 'leaf_index': 0}]}
                    for name in ('one', 'two', 'three')]
            save_json(manifest, {'unresolved': [], 'mission_id': 'bulk', 'rows': rows})
            probes = []

            def resolve(row):
                # Real second connection models the paid pipeline saving an
                # already-generated take while scene/configuration I/O runs.
                with closing(sqlite3.connect(store.path, timeout=.02)) as writer:
                    with writer:
                        writer.execute("UPDATE state SET value=value WHERE name='writer_probe'")
                probes.append(row['audio_hash_en'])
                return (1, 'scene', 1, row['audio_hash_en'])

            configuration = {'chapters': {'bulk': {'scope_kind': 'native_library', 'mission_folder': 'bulk'}}}
            with patch('dub007.campaign.chapter_config', return_value=configuration), \
                 patch('dub007.campaign.scene_key', side_effect=resolve):
                ensure_chapter(store, 'bulk', prepared_path=manifest)
            self.assertGreaterEqual(len(probes), len(rows))
            self.assertEqual(store.database.execute('SELECT COUNT(*) FROM resources').fetchone()[0], 3)
            self.assertEqual(store.database.execute('SELECT COUNT(*) FROM chapter_resources').fetchone()[0], 3)
            self.assertEqual(store.database.execute("SELECT value FROM state WHERE name='writer_probe'").fetchone()[0], 'unchanged')

    def test_progress_excludes_recovered_captionless_speech_from_original_caption_denominator(self):
        import sqlite3
        from types import SimpleNamespace
        from dub007.campaign import ready_captioned_count
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE resources(status TEXT,document TEXT)')
        separator=chr(92)*2
        rows=[{'text_pl_original':'Tak.','text_raw_en':''},
              {'text_pl_original':'','text_raw_en':'//(0,30)'+separator+'Hello.'},
              {'text_pl_original':'','text_raw_en':'//[SPEAKER]'+separator,
               'text_en':'Recovered speech','segments_en':[{'text':'Recovered speech'}]},
              {'text_pl_original':'','text_raw_en':'//(0,30)'+separator}]
        db.executemany('INSERT INTO resources VALUES(?,?)',
                       [('offline_final_wem_validated',json.dumps(row)) for row in rows])
        db.execute('INSERT INTO resources VALUES(?,?)',('needs_agent_adaptation',json.dumps(rows[1])))
        self.assertEqual(ready_captioned_count(SimpleNamespace(database=db),4),2)
        db.close()

    def test_install_snapshot_does_not_revert_newer_retakes_or_script_reviews(self):
        from dub007.deployment import record_installed_rows
        with tempfile.TemporaryDirectory() as folder:
            with patch('dub007.ledger.ARTIFACTS', Path(folder)):
                store = CampaignLedger(Path(folder) / 'ledger.sqlite3')
            store.database.execute('CREATE TABLE resources (audio_hash TEXT PRIMARY KEY, status TEXT, document TEXT)')
            original = {'audio_hash_en': 'one', 'qa_status': 'offline_final_wem_validated',
                        'wem_sha256': 'old', 'text_pl_display': 'Pierwsza wersja.',
                        'install_status': 'awaiting_install'}
            newer = {**original, 'wem_sha256': 'new', 'text_pl_display': 'Poprawiona wersja.',
                     'adaptation_reason': 'Quality retake'}
            with store.database:
                store.database.execute('INSERT INTO resources VALUES (?,?,?)',
                                       ('one', newer['qa_status'], json.dumps(newer)))
            record_installed_rows(store, [original])
            read = lambda: json.loads(store.database.execute('SELECT document FROM resources').fetchone()[0])
            self.assertEqual(read(), newer)
            record_installed_rows(store, [newer])
            self.assertEqual(read()['adaptation_reason'], 'Quality retake')
            self.assertEqual(read()['install_status'], 'installed_new_lines_awaiting_in_game_validation')
            reviewed = {**newer, 'qa_status': 'needs_agent_adaptation', 'tts_audio_tag': 'weakly'}
            with store.database:
                store.database.execute('UPDATE resources SET status=?,document=?',
                                       (reviewed['qa_status'], json.dumps(reviewed)))
            record_installed_rows(store, [newer])
            self.assertEqual(read(), reviewed)
            store.close()

    def test_estimate_reports_campaign_usage_instead_of_the_old_sample_counter(self):
        from dub007.estimate import run as estimate
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_json(root / 'docs/CAMPAIGN_PROGRESS.json', {
                'updated_utc': '2026-10-15T02:00:00+00:00',
                'usage': {'submitted_characters': 1234}})
            save_json(root / 'artifacts/campaign/account-credit-snapshot.json', {
                'checked_unix': 100, 'account': {'character_count': 5000, 'character_limit': 300000}})
            with patch('dub007.estimate.ROOT', root), \
                 patch('dub007.estimate.ARTIFACTS', root / 'artifacts'), \
                 patch('dub007.tts.usage', return_value={'submitted_characters': 414}):
                report = estimate()
            self.assertEqual(report['tts_characters_sent'], 1234)
            self.assertEqual(report['account_observed']['character_count'], 5000)
            self.assertIn('1234', (root / 'docs/ESTIMATE.md').read_text(encoding='utf-8'))

    def test_unnumbered_reactivity_requires_a_reviewed_phase_anchor(self):
        prefix = '[assembly:/voices/bluebell/serverdialogue/'
        row = {'audio_source_path': prefix + 'server_react_secukm01_001.wav].wes',
               'speaker_id': 'SECUKM01', 'audio_hash_en': 'one'}
        config = {'chapters': {'knightfall': {'unnumbered_scene_anchors': [
            {'path_prefix': prefix, 'after': 6481, 'reason': 'Reviewed server-room phase'}]}}}
        with patch('dub007.campaign.chapter_config', return_value=config):
            self.assertEqual(scene_key(row)[:3], (6481, 'server_react_secukm01', 1))
            with self.assertRaisesRegex(ValueError, 'Unknown chronological scene'):
                scene_key({**row, 'audio_source_path': '/unreviewed/other_001.wav'})
            config['chapters']['knightfall']['unnumbered_scene_anchors'][0]['reason'] = ''
            with self.assertRaisesRegex(ValueError, 'Unknown chronological scene'):
                scene_key(row)

    def test_captivity_branches_follow_the_capture_not_their_lower_file_numbers(self):
        names = ['s4420_talktobawmaa', 's4801_gwbawmatalk', 's4700_009dead',
                 's4800_abovethecrocpit', 's4441_icoutro', 's4655_gwboatcheckin1']
        rows = [{'native_audio_label': name + '_bond_001', 'speaker_id': 'BOND',
                 'audio_hash_en': str(index)} for index, name in enumerate(names)]
        rules = [{'prefix': 's', 'first': 4419, 'last': 4441, 'after': 4800}]
        ordered = sorted(rows, key=lambda row: order_key(row, rules))
        self.assertEqual([scene_key(row)[1] for row in ordered],
                         ['s4655_gwboatcheckin1', 's4700_009dead', 's4800_abovethecrocpit',
                          's4420_talktobawmaa', 's4441_icoutro', 's4801_gwbawmatalk'])

    def test_voice_multiplier_budget_and_history_debit_use_credits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            policy = {'working_value_limit_pln': 1, 'pricing_usd_per_1000': .08,
                      'usd_pln_mid': 4, 'tax_assumed': .23, 'voice_credit_multipliers': {'premium': 3}}
            save_json(root / 'config/campaign.json', policy)
            with patch('dub007.ledger.ROOT', root), patch('dub007.ledger.ARTIFACTS', root / 'artifacts'):
                store = CampaignLedger(root / 'ledger.sqlite3')
                identity = {'audio_hash_en': 'one', 'voice_id': 'premium', 'request': {'text': 'a' * 1000}}
                with self.assertRaisesRegex(RuntimeError, 'budget exhausted'):
                    store.reserve(identity)
                self.assertEqual(store.usage()['requests'], 0)
                policy['working_value_limit_pln'] = 2
                save_json(root / 'config/campaign.json', policy)
                entry = store.reserve(identity)
                entry.update(status='succeeded', request_id='request-one')
                store.write({'entries': [entry]})
                self.assertEqual(store.usage()['accounted_credits'], 3000)
                with patch('dub007.billing.get', return_value={'history': [
                    {'request_id': 'request-one', 'character_count_change_from': 100, 'character_count_change_to': 2005}]}), \
                     patch('dub007.billing.api_key', return_value='unused-test-key'):
                    self.assertEqual(reconcile(store)['pending'], 0)
                self.assertEqual(store.usage()['successful_characters'], 1000)
                self.assertEqual(store.usage()['accounted_credits'], 1905)
                self.assertEqual(store.quota_credits(), 1905)
                self.assertEqual(store.budget_credits(), 3000)
                store.close()
                reopened = CampaignLedger(root / 'ledger.sqlite3')
                self.assertEqual(reopened.usage()['history_verified_credits'], 1905)
                reopened.close()

    def test_recovered_multisegment_take_without_alignment_is_not_fitted_globally(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CampaignLedger(Path(folder) / 'ledger.sqlite3')
            with patch('dub007.campaign.CampaignLedger', return_value=store):
                database()
            row = {'audio_hash_en': 'one', 'qa_status': 'original_metadata_verified',
                   'segments_pl': [{'text': 'First'}, {'text': 'Second'}], 'original_markers': {}}
            store.database.execute('INSERT INTO resources VALUES(?,?,?,?,?,?)',
                                   ('one', 0, 'chapter', 'scene', row['qa_status'], json.dumps(row)))
            store.database.execute('INSERT INTO chapter_resources VALUES(?,?,?)', ('chapter', 'one', 0))
            store.database.commit()
            with patch('dub007.campaign.initialize', return_value=store), patch('dub007.campaign.ensure_chapter'), \
                 patch('dub007.campaign.original_wav'), patch('dub007.campaign.progress', return_value={}), \
                 patch('dub007.campaign.generate', return_value={'audio_path': 'unused.wav', 'cache_key': 'recovered'}), \
                 patch('dub007.campaign.fit_audio') as fit, patch('dub007.campaign.encode') as encode:
                report = run(chapter='chapter')
            self.assertIn('needs alignment', report['needs_attention']['qa_error'])
            fit.assert_not_called()
            encode.assert_not_called()

    def test_native_label_fills_missing_public_chapter_path(self):
        row = {'audio_source_path': None, 'dialogue_source_path': None,
               'native_audio_label': 's13612_bandmpaboutrothonly_moneypenny_003',
               'speaker_id': 'MONEYPENNY', 'audio_hash_en': '0127A6A999FDA8FA'}
        self.assertEqual(scene_key(row)[:3], (13612, 's13612_bandmpaboutrothonly', 3))

    def test_changed_night_budget_is_checked_before_reserving_another_request(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "config/campaign.json"
            policy = {"working_value_limit_pln": 1000, "pricing_usd_per_1000": 0.08,
                      "usd_pln_mid": 4, "tax_assumed": 0.23, "max_versions_per_resource": 3}
            save_json(config, policy)
            with patch("dub007.ledger.ROOT", root), patch("dub007.ledger.ARTIFACTS", root / "artifacts"):
                store = CampaignLedger(root / "ledger.sqlite3")
                entry = store.reserve({"audio_hash_en": "one", "request": {"text": "a" * 2000}})
                entry["status"] = "succeeded"
                store.write({"entries": [entry]})
                policy["working_value_limit_pln"] = 1
                save_json(config, policy)
                with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                    store.reserve({"audio_hash_en": "two", "request": {"text": "a" * 600}})
                self.assertEqual(store.usage()["requests"], 1)
                self.assertLess(store.usage()["estimated_usage_value_pln"], 1)
                store.close()

    def test_expired_overnight_deadline_does_not_reserve_paid_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_json(root / "config/campaign.json", {"night_run": {"deadline": "2000-01-01T07:00:00+01:00"}})
            with patch("dub007.ledger.ROOT", root), patch("dub007.ledger.ARTIFACTS", root / "artifacts"):
                store = CampaignLedger(root / "ledger.sqlite3")
                with self.assertRaisesRegex(RuntimeError, "deadline reached"):
                    store.reserve({"audio_hash_en": "one", "request": {"text": "a"}})
                self.assertEqual(store.database.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 0)
                store.close()
    def test_chapter_switch_does_not_generate_paused_clover(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = CampaignLedger(root / "ledger.sqlite3")
            with patch("dub007.campaign.CampaignLedger", return_value=store):
                database()
            for position, chapter in enumerate(("clover", "out-of-the-ashes")):
                row = {"audio_hash_en": chapter, "qa_status": "needs_agent_script_review", "text_en": "[Review]"}
                store.database.execute("INSERT INTO resources VALUES(?,?,?,?,?,?)", (chapter, position, chapter, chapter, row["qa_status"], json.dumps(row)))
                store.database.execute("INSERT INTO chapter_resources VALUES(?,?,?)", (chapter, chapter, position))
            store.database.commit()
            with patch("dub007.campaign.initialize", return_value=store), patch("dub007.campaign.ensure_chapter"), \
                 patch("dub007.campaign.progress", return_value={}), patch("dub007.campaign.generate") as paid:
                report = run(chapter="out-of-the-ashes")
            self.assertEqual(report["needs_script_review"]["audio_hash_en"], "out-of-the-ashes")
            paid.assert_not_called()
            store = CampaignLedger(root / "ledger.sqlite3")
            with patch("dub007.campaign.initialize", return_value=store), patch("dub007.campaign.ensure_chapter"), \
                 patch("dub007.campaign.progress", return_value={}), patch("dub007.campaign.generate") as paid:
                report = run(chapter="out-of-the-ashes", audio_hashes=["clover"])
                self.assertEqual(report["needs_script_review"]["audio_hash_en"], "clover")
            store = CampaignLedger(root / "ledger.sqlite3")
            with patch("dub007.campaign.initialize", return_value=store), patch("dub007.campaign.ensure_chapter"), \
                 patch("dub007.campaign.generate") as paid:
                with self.assertRaisesRegex(ValueError, "Unknown repair"):
                    run(chapter="out-of-the-ashes", audio_hashes=["missing"])
            paid.assert_not_called()
            store.close()
    def test_scene_order_keeps_first_cinematic_line_order(self):
        first = {"audio_source_path": "[assembly:/voices/english(us)/clover/clover_story/s1005_intro_bond_001.wav].wes", "speaker_id": "BOND", "audio_hash_en": "a"}
        second = {**first, "audio_source_path": "[assembly:/voices/english(us)/clover/clover_story/s1005_intro_bond_002_alt3.wav].wes"}
        later = {**first, "audio_source_path": "[assembly:/voices/english(us)/clover/clover_story/s1008_execution_bond_001.wav].wes"}
        self.assertLess(scene_key(first), scene_key(second))
        self.assertLess(scene_key(second), scene_key(later))

    def test_sqlite_unknown_request_blocks_duplicate_and_survives_reopen(self):
        with tempfile.TemporaryDirectory() as folder, patch('dub007.ledger.ROOT', Path(folder)):
            save_json(Path(folder) / 'config/campaign.json', {
                'working_value_limit_pln': 200, 'pricing_usd_per_1000': .08,
                'usd_pln_mid': 4, 'tax_assumed': .23})
            location = Path(folder) / "tts.sqlite3"
            store = CampaignLedger(location)
            identity = {"audio_hash_en": "test-only-hash", "request": {"text": "Tekst"}}
            entry = store.reserve(identity)
            entry["status"] = "unknown"
            store.write({"entries": [entry]})
            before = store.usage()["submitted_characters"]
            store.close()
            reopened = CampaignLedger(location)
            self.assertEqual(reopened.usage()["submitted_characters"], before)
            with self.assertRaisesRegex(RuntimeError, "Unresolved"):
                reopened.reserve(identity)
            reopened.close()

    def test_rejected_requests_count_for_submission_but_not_usage_value(self):
        with tempfile.TemporaryDirectory() as folder, patch('dub007.ledger.ROOT', Path(folder)):
            save_json(Path(folder) / 'config/campaign.json', {
                'working_value_limit_pln': 200, 'pricing_usd_per_1000': .08,
                'usd_pln_mid': 4, 'tax_assumed': .23})
            store = CampaignLedger(Path(folder) / "ledger.sqlite3")
            before = store.usage()
            entry = store.reserve({"audio_hash_en": "invalid-test-hash", "request": {"text": "[bad]"}})
            entry["status"] = "http_rejected"
            store.write({"entries": [entry]})
            after = store.usage()
            self.assertEqual(after["submitted_characters"], before["submitted_characters"] + 5)
            self.assertEqual(after["estimated_usage_value_pln"], before["estimated_usage_value_pln"])
            store.close()


class PlanTests(unittest.TestCase):
    setUp = fixtures.OfflineTests.setUp
    tearDown = fixtures.OfflineTests.tearDown
    make_bundle = fixtures.OfflineTests.make_bundle
    install = fixtures.OfflineTests.install

    def test_recover_installed_deployment_without_published_pointer(self):
        path = self.make_bundle()
        journal = self.install(path)
        artifact = self.root
        deployment_path = artifact / "campaign/deployment.json"
        current = artifact / "campaign/current-install.json"
        save_json(deployment_path, {"status": "old_removed", "new_install_id": journal["install_id"], "old_install_id": None})
        manifest = (self.runtime / "packagedefinition.txt").read_bytes()
        with patch("dub007.deployment.ROOT", self.game), patch("dub007.deployment.ARTIFACTS", artifact), \
             patch("dub007.deployment.DEPLOYMENT", deployment_path), patch("dub007.deployment.CURRENT", current):
            self.assertEqual(recover_pending(dry_run=True)["status"], "pending_deployment_recovery")
            self.assertFalse(current.exists())
            self.assertEqual(recover_pending()["status"], "recovered_installed")
        self.assertEqual(json.loads(current.read_text())["install_id"], journal["install_id"])
        self.assertEqual((self.runtime / "packagedefinition.txt").read_bytes(), manifest)

    def test_recover_partial_new_install_restores_clean_state(self):
        path = self.make_bundle()
        with self.assertRaises(RuntimeError):
            self.install(path, interrupt_after="patches_written")
        identifier = next(self.backups.iterdir()).name
        deployment_path = self.root / "campaign/deployment.json"
        save_json(deployment_path, {"status": "old_removed", "new_install_id": identifier, "old_install_id": None})
        def restore(identifier):
            return rollback(identifier, backup_root=self.backups, expected_game_root=self.game)
        with patch("dub007.deployment.ROOT", self.game), patch("dub007.deployment.ARTIFACTS", self.root), \
             patch("dub007.deployment.DEPLOYMENT", deployment_path), patch("dub007.deployment.rollback", side_effect=restore):
            self.assertEqual(recover_pending()["status"], "recovered_clean")
        self.assertEqual((self.runtime / "packagedefinition.txt").read_bytes(), self.original)
        self.assertFalse((self.runtime / "chunk0patch1.rpkg").exists())

    def test_virtual_replacement_dry_run_keeps_actual_files_unchanged(self):
        path = self.make_bundle()
        installed = self.install(path)
        actual_manifest = (self.runtime / "packagedefinition.txt").read_bytes()
        result = plan(path, self.game, definition_override=self.original,
                      ignored_existing={installed["patches"][0]["filename"]: installed["patches"][0]["sha256"]})[0]
        self.assertEqual(result["before_sha256"], installed["before_sha256"])
        self.assertEqual((self.runtime / "packagedefinition.txt").read_bytes(), actual_manifest)
        with self.assertRaisesRegex(ValueError, "already exists"):
            plan(path, self.game, ignored_existing={installed["patches"][0]["filename"]: "wrong checksum"})


if __name__ == "__main__":
    unittest.main()
