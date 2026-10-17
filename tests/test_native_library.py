import unittest
import sqlite3
import struct

from dub007.native import library_scene_key, validate_library_owners, complete_library_manifest


class NativeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.row = {'audio_hash_en': 'audio', 'line_id': 'dialogue', 'speaker_id': 'BOND',
                    'native_audio_label': 'bondspeakingnew_tracephone_bond_003'}
        self.configuration = {'scope_kind': 'native_library', 'source_dlge_hashes': ['dialogue'],
                              'reviewed_native_labels': {'audio': self.row['native_audio_label']}}

    def test_shared_library_has_label_order_without_a_numbered_scene(self):
        self.assertEqual(library_scene_key(self.row, self.configuration),
                         (0, 'bondspeakingnew_tracephone', 3, 'audio'))

    def test_shared_library_rejects_changed_audio_dialogue_or_native_label(self):
        for field, value in [('audio_hash_en', 'other'), ('line_id', 'other'),
                             ('native_audio_label', 'other_bond_003'), ('speaker_id', 'Q')]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                library_scene_key({**self.row, field: value}, self.configuration)

    def test_unreviewed_scope_cannot_assign_shared_library_order(self):
        with self.assertRaises(ValueError):
            library_scene_key(self.row, {**self.configuration, 'scope_kind': 'mapped_story_scope'})

    def test_exact_reviewed_alternate_label_remains_a_distinct_audio_resource(self):
        alternate={**self.row,'native_audio_label':self.row['native_audio_label']+'_alt1'}
        config={**self.configuration,'reviewed_native_labels':{'audio':alternate['native_audio_label']}}
        self.assertEqual(library_scene_key(alternate,config),(0,'bondspeakingnew_tracephone',3,'audio'))
        with self.assertRaises(ValueError):
            library_scene_key(alternate,self.configuration)

    def test_interrupted_preparation_cannot_be_reused_as_complete(self):
        self.assertFalse(complete_library_manifest({'rows':[self.row]},'scope',self.configuration))
        completed={**self.row,'native_library_scope':'scope'}
        self.assertTrue(complete_library_manifest({'rows':[completed]},'scope',self.configuration))
        changed={**self.configuration,'reviewed_native_labels':{**self.configuration['reviewed_native_labels'],'missing':'another_bond_001'}}
        self.assertFalse(complete_library_manifest({'rows':[completed]},'scope',changed))

    def test_multiple_owners_require_every_dialogue_reference(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE resources(hash TEXT,type TEXT,refs BLOB)')
        raw = lambda h: struct.pack('<I',1)+b'\x5f'+struct.pack('<Q',int(h,16))
        db.executemany('INSERT INTO resources VALUES(?,?,?)',
                       [('ownerA','TEMP',raw('1')),('ownerB','TEMP',raw('2'))])
        config = {'owner_temp_hashes':['ownerA','ownerB'],
                  'source_dlge_hashes':['0000000000000001','0000000000000002']}
        self.assertEqual(validate_library_owners(db,config),['ownerA','ownerB'])
        db.execute("UPDATE resources SET refs=? WHERE hash='ownerB'",(raw('3'),))
        with self.assertRaisesRegex(ValueError,'all reviewed dialogue'):
            validate_library_owners(db,config)
        db.close()

    def test_missing_or_ambiguous_owner_blocks_native_library(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE resources(hash TEXT,type TEXT,refs BLOB)')
        config={'owner_temp_hash':'owner','source_dlge_hashes':['0000000000000001']}
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):
            validate_library_owners(db,config)
        raw=struct.pack('<I',1)+b'\x5f'+struct.pack('<Q',1)
        db.execute('INSERT INTO resources VALUES(?,?,?)',('owner','TEMP',raw))
        self.assertEqual(validate_library_owners(db,config),['owner'])
        db.execute('INSERT INTO resources VALUES(?,?,?)',('owner','TEMP',raw))
        with self.assertRaisesRegex(ValueError,'missing or ambiguous'):
            validate_library_owners(db,config)
        db.close()
