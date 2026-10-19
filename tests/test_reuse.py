import copy
import tempfile
import json
import wave
from pathlib import Path
import unittest
from unittest.mock import patch

from dub007.reuse import compatible, clone, original_pcm
from dub007.common import sha256


def row(h, ready=False):
    return {'audio_hash_en':h,'qa_status':'offline_final_wem_validated' if ready else 'original_metadata_verified',
            'generation_status':'generated' if ready else 'not_started','speaker_id':'CIVUKM01',
            'voice_id':'verified-voice','model_id':'verified-model','settings':{'stability':.5},
            'sample_rate':24000,'sample_count':24000,'channels':1,
            'audio_only_translation':{'text_pl':'Cześć.'},'source_audio_review':{'evidence':'reviewed'},
            'speaker_recovery_evidence':{'status':'actor_source_proven','speaker':'CIVUKM01'},
            'text_raw_en':'','text_raw_pl':'','text_pl_original':'','text_pl_display':'',
            'segments_pl':[{'segment_id':0,'timing_raw':'0,1','text':'Cześć.'}],
            'original_markers':{'cue ':{'points':[{'sample_offset':0}]}}}


class AudioReuseTests(unittest.TestCase):
    def test_requires_same_actor_casting_model_settings_and_original_profile(self):
        source,target=row('a',True),row('b')
        self.assertTrue(compatible(source,target))
        for key,value in [('speaker_id','CIVUKM02'),('voice_id','other'),('model_id','other'),
                          ('settings',{'stability':.8}),('sample_count',23000),('sample_rate',48000)]:
            changed=copy.deepcopy(target);changed[key]=value
            self.assertFalse(compatible(source,changed),key)

    def test_rejects_captions_internal_sync_loops_foreign_spans_and_started_targets(self):
        source,target=row('a',True),row('b')
        for key,value in [('text_raw_pl','original caption'),('text_raw_en','original English'),
                          ('preserve_original_prefix_until',.2),('preserve_original_segment_indices',[0]),
                          ('generation_status','generated'),('forbid_audio_reuse',True)]:
            changed=copy.deepcopy(target);changed[key]=value
            self.assertFalse(compatible(source,changed),key)
        for markers in ({'cue ':{'points':[{'sample_offset':10}]}},
                        {'cue ':{'points':[{'sample_offset':0},{'sample_offset':100}]}},
                        {'cue ':{'points':[{'sample_offset':0}]},'smpl':{}}):
            changed=copy.deepcopy(target);changed['original_markers']=markers
            self.assertFalse(compatible(source,changed))

    def test_text_match_does_not_allow_different_original_audio(self):
        source,target=row('a',True),row('b')
        proof={'sample_rate':24000,'channels':1,'sample_count':24000,'sample_width':2,'pcm_sha256':'first'}
        other={**proof,'pcm_sha256':'different'}
        with patch('dub007.reuse.original_pcm',side_effect=[proof,other]),patch('dub007.audio.validate_encoded') as encode:
            with self.assertRaisesRegex(ValueError,'Original audio differs'):
                clone(source,target)
            encode.assert_not_called()

    def test_original_checksum_change_rejected_before_decoder_or_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'original.wem').write_bytes(b'changed')
            target=row('b');target.update(original_wem_path='original.wem',original_payload_sha256='wrong')
            with patch('dub007.reuse.ROOT',root),patch('dub007.reuse.subprocess.run') as decode:
                with self.assertRaisesRegex(ValueError,'checksum changed'):
                    original_pcm(target)
                decode.assert_not_called()

    def test_cached_pcm_digest_cannot_override_actual_decoded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);original=root/'original.wem';original.write_bytes(b'original')
            digest=sha256(original.read_bytes());folder=root/'campaign/pcm-proof';folder.mkdir(parents=True)
            wav=folder/(digest+'.wav')
            with wave.open(str(wav),'wb') as stream:
                stream.setparams((1,2,24000,24000,'NONE','not compressed'));stream.writeframes(b'\x01\x00'*24000)
            proof={'original_wem_sha256':digest,'wav_sha256':sha256(wav.read_bytes()),'sample_rate':24000,
                   'channels':1,'sample_count':24000,'sample_width':2,'pcm_sha256':'forged'}
            (folder/(digest+'.json')).write_text(json.dumps(proof),encoding='utf-8')
            target=row('b');target.update(original_wem_path='original.wem',original_payload_sha256=digest)
            with patch('dub007.reuse.ROOT',root),patch('dub007.reuse.ARTIFACTS',root),patch('dub007.reuse.subprocess.run') as decode:
                with self.assertRaisesRegex(ValueError,'metadata changed'):
                    original_pcm(target)
                decode.assert_not_called()
