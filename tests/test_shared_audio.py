from array import array
import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dub007.audio import prepare_campaign, read_wav, write_wav
from dub007.common import save_json
from dub007.shared_audio import prepare_shared


class SharedAudioTests(unittest.TestCase):
    def fixture(self, root):
        save_json(root/'config/campaign.json',{'shared_audio_fitting':{
            'enabled':True,'reviewed_scopes':['scope'],'maximum_extension_seconds':.2}})
        save_json(root/'config/chapters.json',{'chapters':{'scope':{
            'scope_kind':'reactive','reactive_speaker':'BOND',
            'reactive_audio_prefix':'audio/bond/','reactive_dialogue_prefix':'dialogue/bond/',
            'reactive_location_tokens':['bond']}}})
        original,generated=root/'original.wav',root/'raw.wav'
        write_wav(original,1000,array('h',[0]*20+[1000]*450+[0]*30))
        write_wav(generated,1000,array('h',[0]*100+[1000]*620+[0]*100))
        row={'audio_hash_en':'line','speaker_id':'BOND','reactive_scope':'scope','chapter_id':'scope',
             'resource_type':'WWES','audio_mapping_status':'reference_verified',
             'original_payload_sha256':'a'*64,'sample_rate':1000,'channels':1,'sample_count':500,
             'original_wav_path':str(original),'original_rms_dbfs':-30,'scene_line_number':1,
             'segments_pl':[{}],'original_markers':{'cue ':{'points':[{'sample_offset':0}]}},
             'native_audio_label':'reply_bond_001','audio_source_path':'audio/bond/reply_bond_001.wav',
             'dialogue_source_path':'dialogue/bond/reply.sweetdialog'}
        return row,generated

    def test_bounded_extension_retains_every_speech_sample_without_new_generation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);row,generated=self.fixture(root)
            resamples=[]
            def conversion(args,**kwargs):
                if '-af' in args:
                    raise ValueError('Synthetic test permits unchanged speech samples only')
                resamples.append(args)
                shutil.copyfile(generated,args[-1])
            with patch('dub007.shared_audio.ROOT',root),patch('dub007.audio.ffmpeg',return_value='ffmpeg'),patch('dub007.audio.subprocess.run',side_effect=conversion):
                with self.assertRaises(ValueError) as failed:
                    prepare_campaign(row,generated)
                before=len(resamples)
                final=prepare_shared(row,generated,failed.exception)
                self.assertEqual(len(resamples)-before,1)
            rate,samples=read_wav(final)
            self.assertEqual(rate,1000)
            self.assertGreater(len(samples),500)
            self.assertLessEqual(len(samples),700)
            self.assertEqual(sum(v!=0 for v in samples),620)
            self.assertGreaterEqual(row['timing_qa']['tail_seconds'],.01)
            self.assertEqual(row['shared_fit_review']['new_paid_requests'],0)

    def test_unproven_mapping_internal_cues_loops_or_non_timing_errors_cannot_extend(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);row,generated=self.fixture(root)
            changes=[{'reactive_scope':'unreviewed'},{'channels':2},
                     {'audio_mapping_status':'unverified'},
                     {'original_markers':{'cue ':{'points':[{'sample_offset':1}]}}},
                     {'original_markers':{**row['original_markers'],'smpl':{}}},
                     {'segments_pl':[{},{}]}]
            with patch('dub007.shared_audio.ROOT',root),patch('dub007.shared_audio.prepare',side_effect=AssertionError('Must reject before conversion')):
                for changed in changes:
                    with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'needs shorter'):
                        prepare_shared({**copy.deepcopy(row),**changed},generated,ValueError('needs shorter text or retake'))
                with self.assertRaisesRegex(ValueError,'silent'):
                    prepare_shared(row,generated,ValueError('Audio is silent'))
                config_path=root/'config/chapters.json'
                import json
                config=json.loads(config_path.read_text(encoding='utf-8'))
                config['chapters']['scope']['scope_kind']='mapped_story_scope'
                save_json(config_path,config)
                with self.assertRaisesRegex(ValueError,'needs shorter'):
                    prepare_shared(row,generated,ValueError('needs shorter text or retake'))

    def test_reviewed_source_only_caption_can_fit_without_fabricating_original_segments(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);row,generated=self.fixture(root)
            row.update(segments_pl=[],text_pl_original='',text_pl_display='Tak.',
                       translation_source='agent_en_fallback_missing_pl',
                       display_segments=['Tak.'],
                       reviewed_subtitle_segments=[{'timing_raw':'0,0.5','text':'Tak.'}],
                       subtitle_timing_override_reason='Individually reviewed header-only PL insertion.',
                       source_audio_review={'evidence':'original-audio-review'})
            def conversion(args,**kwargs):
                if '-af' in args:raise ValueError('Retain every synthetic speech sample')
                shutil.copyfile(generated,args[-1])
            with patch('dub007.shared_audio.ROOT',root),patch('dub007.audio.ffmpeg',return_value='ffmpeg'),patch('dub007.audio.subprocess.run',side_effect=conversion):
                final=prepare_shared(row,generated,ValueError('needs shorter text or retake'))
            _,samples=read_wav(final)
            self.assertEqual(sum(v!=0 for v in samples),620)
            self.assertLessEqual(len(samples),700)
            self.assertEqual(row['segments_pl'],[])
            self.assertEqual(row['text_pl_original'],'')
            self.assertGreaterEqual(row['timing_qa']['tail_seconds'],.01)
            self.assertEqual(row['shared_fit_review']['new_paid_requests'],0)

    def test_missing_or_inconsistent_new_caption_review_blocks_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);row,generated=self.fixture(root)
            row.update(segments_pl=[],text_pl_original='',text_pl_display='Tak.',
                       translation_source='agent_en_fallback_missing_pl',display_segments=['Tak.'],
                       reviewed_subtitle_segments=[{'text':'Tak.'}],
                       subtitle_timing_override_reason='Reviewed',source_audio_review={'evidence':'review'})
            changes=[{'source_audio_review':None},{'subtitle_timing_override_reason':''},
                     {'reviewed_subtitle_segments':[{'text':'Other'}]},
                     {'reviewed_subtitle_segments':[{'text':'Tak.'},{'text':'Tak.'}]},
                     {'text_pl_original':'Existing caption'}, {'display_segments':[]}]
            with patch('dub007.shared_audio.ROOT',root),patch('dub007.shared_audio.prepare',side_effect=AssertionError('Reject before conversion')):
                for changed in changes:
                    with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'needs shorter'):
                        prepare_shared({**row,**changed},generated,ValueError('needs shorter text or retake'))

    def test_speech_that_exceeds_the_cap_is_held_without_modifying_duration(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);row,generated=self.fixture(root)
            write_wav(generated,1000,array('h',[0]*100+[1000]*1500+[0]*100))
            with patch('dub007.shared_audio.ROOT',root),patch('dub007.audio.ffmpeg',return_value='ffmpeg'),patch('dub007.audio.subprocess.run',side_effect=lambda args,**kwargs:shutil.copyfile(generated,args[-1])):
                with self.assertRaisesRegex(ValueError,'needs shorter'):
                    prepare_shared(row,generated,ValueError('needs shorter text or retake'))
            self.assertEqual(row['sample_count'],500)
            self.assertNotIn('reviewed_duration_extension_seconds',row)
