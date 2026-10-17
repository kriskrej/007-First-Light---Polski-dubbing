import json
import unittest

from dub007.archive import open_index, read_resource, refs
from dub007.common import ARTIFACTS, ROOT
from dub007.dlge import parse, replace_polish
from dub007.subtitles import replacement_text, resources


class SubtitleTests(unittest.TestCase):
    def test_missing_polish_text_requires_review_and_retains_original_header(self):
        original = '//[ASSISTANT]' + chr(92) * 2 + '//{ASSISTANT}' + chr(92) * 2
        row = {'chapter_id': 'wave-of-the-future', 'duration': 5,
               'text_pl_original': '', 'translation_source': 'agent_en_fallback_missing_pl',
               'subtitle_timing_override_reason': 'Agent translation from verified English; PL block has headers only.',
               'reviewed_subtitle_segments': [{'timing_raw': '0,30'}]}
        result = replacement_text(row, original, ['Priorytet zero.'])
        self.assertEqual(result, original + '//(0,30)' + chr(92) * 2 + 'Priorytet zero.')
        row['translation_source'] = 'official_pl'
        with self.assertRaisesRegex(ValueError, 'no timing'):
            replacement_text(row, original, ['Priorytet zero.'])

    def test_real_empty_polish_block_accepts_reviewed_translation_and_exact_rollback(self):
        if not (ROOT / 'Runtime' / 'chunk0.rpkg').is_file():
            self.skipTest('Local game resources required')
        database = open_index()
        try:
            entry = database.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", ('016208641A296942',)).fetchone()
            if entry is None:
                self.skipTest('Local final-chapter resource required')
            with (ROOT / 'Runtime' / entry['archive']).open('rb') as stream:
                original = read_resource(stream, entry)
            references = refs(entry)
            leaf = parse(original, references)['leaves'][0]
            old = leaf['languages']['pl']['text_raw']
            self.assertFalse(leaf['languages']['pl']['segments'])
            row = {'chapter_id': 'wave-of-the-future', 'duration': 12.311125,
                   'text_pl_original': '', 'translation_source': 'agent_en_fallback_missing_pl',
                   'subtitle_timing_override_reason': 'Reviewed missing PL translation.',
                   'reviewed_subtitle_segments': [{'timing_raw': '0,2.3404908'},
                       {'timing_raw': '2.3404908,6.38957'}, {'timing_raw': '6.38957,36.389572'}]}
            replacement = replacement_text(row, old, ['Priorytet zero.', 'Dzwoni wiceminister.', 'Pyta o tuszowanie sprawy.'])
            patched = replace_polish(original, references, 0, old, replacement)
            self.assertEqual(replace_polish(patched, references, 0, replacement, old), original)
        finally:
            database.close()

    def test_timing_only_polish_change_is_included_in_the_cumulative_patch(self):
        manifest = ARTIFACTS / 'chapters/uninvited/manifest.json'
        if not manifest.exists():
            self.skipTest('Local Uninvited resources required')
        row = next(r for r in json.loads(manifest.read_text(encoding='utf-8'))['rows']
                   if r['audio_hash_en'] == '01A2E6B06A67273D')
        row.update(chapter_id='uninvited', text_pl_display=row['text_pl_original'],
                   subtitle_timing_override_reason='Reviewed 100 ms earlier onset for Polish randce.')
        row['reviewed_subtitle_segments'] = [dict(s) for s in row['segments_pl']]
        row['reviewed_subtitle_segments'][1]['timing_raw'] = '3.1,33.1'
        output = resources([row])
        self.assertEqual(len(output), 1)
        item = output[0]
        references = refs({'refs': item['refs']})
        actual = parse(item['payload'], references)['leaves'][row['leaf_index']]['languages']['pl']['text_raw']
        self.assertIn('//(3.1,33.1)', actual)
        reversed_payload = replace_polish(item['payload'], references, row['leaf_index'], actual, row['text_raw_pl'])
        from dub007.common import sha256
        self.assertEqual(sha256(reversed_payload), row['original_dlge_payload_sha256'])

    def test_real_switch_dialogues_keep_three_audio_variants_when_polish_changes(self):
        if not (ARTIFACTS / "poc/manifest.json").exists():
            self.skipTest("Local game resources required")
        database = open_index()
        try:
            for resource_hash in ['01152DF67C77921E', '015CD7B0BC739910']:
                entry = database.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", (resource_hash,)).fetchone()
                with (ROOT / 'Runtime' / entry['archive']).open('rb') as stream:
                    raw = read_resource(stream, entry)
                references = refs(entry)
                parsed = parse(raw, references)
                leaf = parsed['leaves'][0]
                self.assertEqual(leaf['languages']['en']['audio_ref_indices'], [4, 5, 6])
                for audio_hash in leaf['languages']['en']['audio_hashes']:
                    self.assertEqual(database.execute('SELECT type FROM resources WHERE hash=?', (audio_hash,)).fetchone()[0], 'WWES')
                original = leaf['languages']['pl']['text_raw']
                patched = replace_polish(raw, references, 0, original, original + ' QA')
                final = parse(patched, references)['leaves'][0]
                self.assertEqual(final['languages']['en']['audio_hashes'], leaf['languages']['en']['audio_hashes'])
                self.assertEqual(replace_polish(patched, references, 0, original + ' QA', original), raw)
        finally:
            database.close()

    def test_add_missing_english_suffix_only_to_reviewed_polish_caption(self):
        original = "//[B]\\\\//(0,30)\\\\[w hindi]"
        row = {"chapter_id": "the-past-never-dies", "duration": 19.52,
               "subtitle_timing_override_reason": "English suffix omitted from original PL.",
               "reviewed_subtitle_segments": [{"timing_raw": "0,17.2"}, {"timing_raw": "18.1,48.1"}]}
        result = replacement_text(row, original, ["[w hindi]", "Do końca życia."])
        self.assertEqual(result, "//[B]\\\\//(0,17.2)\\\\[w hindi]//(18.1,48.1)\\\\Do końca życia.")
        row.pop("chapter_id")
        with self.assertRaisesRegex(ValueError, "review"):
            replacement_text(row, original, ["[w hindi]", "Do końca życia."])

    def test_copied_real_dlge_polish_edit_and_reverse(self):
        manifest = ARTIFACTS / "poc" / "manifest.json"
        if not manifest.exists():
            self.skipTest("Local extracted sample required")
        row = json.loads(manifest.read_text(encoding="utf-8"))["rows"][3]
        database = open_index()
        entry = database.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", (row["line_id"],)).fetchone()
        database.close()
        with (ROOT / "Runtime" / entry["archive"]).open("rb") as stream:
            original = read_resource(stream, entry)
        references = refs(entry)
        text = row["text_raw_pl"]
        replacement = text.replace(row["text_pl_original"], "Co mamy?")
        patched = replace_polish(original, references, row["leaf_index"], text, replacement)
        parsed = parse(patched, references)
        self.assertEqual(parsed["leaves"][row["leaf_index"]]["languages"]["pl"]["text_raw"], replacement)
        restored = replace_polish(patched, references, row["leaf_index"], replacement, text)
        self.assertEqual(restored, original)
        with self.assertRaisesRegex(ValueError, "differs"):
            replace_polish(original, references, row["leaf_index"], "wrong source", replacement)


if __name__ == "__main__":
    unittest.main()
