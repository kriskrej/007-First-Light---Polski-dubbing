"""Reuse reviewed captionless performances only after original PCM identity proof."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import wave

from .common import ARTIFACTS, ROOT, save_json, sha256


def compatible(source, target):
    """Actor, casting and simple original timing must agree before decoding."""
    if (source.get('qa_status') != 'offline_final_wem_validated'
            or target.get('qa_status') != 'original_metadata_verified'
            or target.get('generation_status') != 'not_started'
            or source.get('audio_hash_en') == target.get('audio_hash_en')):
        return False
    return same_context(source, target)


def same_context(source, target):
    """Check performance inputs independently of a paid take's processing state."""
    for row in (source, target):
        cues = row.get('original_markers', {}).get('cue ', {}).get('points', [])
        if (not row.get('audio_only_translation') or not row.get('source_audio_review')
                or row.get('speaker_recovery_evidence', {}).get('status') != 'actor_source_proven'
                or row['speaker_recovery_evidence'].get('speaker') != row.get('speaker_id')
                or row.get('text_pl_original') or row.get('text_pl_display')
                or row.get('text_raw_en') or row.get('text_raw_pl')
                or row.get('channels') != 1 or len(row.get('segments_pl', [])) != 1
                or len(cues) != 1 or cues[0]['sample_offset'] != 0
                or 'smpl' in row['original_markers']
                or row.get('preserve_original_segment_indices')
                or row.get('preserve_original_prefix_until') or row.get('forbid_audio_reuse')):
            return False
    return all(source.get(key) == target.get(key) for key in
               ('speaker_id', 'voice_id', 'model_id', 'settings', 'sample_rate', 'sample_count'))


def original_pcm(row):
    """Cache a fresh decode by immutable WEM checksum, verifying cached bytes too."""
    original = ROOT / row['original_wem_path']
    digest = sha256(original.read_bytes())
    if digest != row['original_payload_sha256']:
        raise ValueError('Original duplicate candidate WEM checksum changed')
    folder = ARTIFACTS / 'campaign/pcm-proof'
    folder.mkdir(parents=True, exist_ok=True)
    wav, report = folder / (digest + '.wav'), folder / (digest + '.json')
    previous = None
    if report.exists():
        previous = json.loads(report.read_text(encoding='utf-8'))
        if previous['original_wem_sha256'] != digest or sha256(wav.read_bytes()) != previous['wav_sha256']:
            raise ValueError('Original PCM proof cache changed')
    else:
        subprocess.run([str(ROOT / 'research/vgmstream-win64/vgmstream-cli.exe'), '-i', '-o', str(wav), str(original)],
                       check=True, capture_output=True)
    with wave.open(str(wav), 'rb') as stream:
        profile = (stream.getframerate(), stream.getnchannels(), stream.getnframes(), stream.getsampwidth())
        pcm = stream.readframes(stream.getnframes())
    if profile != (row['sample_rate'], 1, row['sample_count'], 2):
        raise ValueError('Original PCM duplicate decoder profile mismatch')
    if not any(pcm):
        raise ValueError('Silent resources do not require a Polish performance')
    proof = {'original_wem_sha256':digest, 'wav_sha256':sha256(wav.read_bytes()),
             'sample_rate':profile[0], 'channels':profile[1], 'sample_count':profile[2],
             'sample_width':profile[3], 'pcm_sha256':sha256(pcm)}
    if previous is not None and previous != proof:
        raise ValueError('Original PCM proof metadata changed')
    if previous is None:
        save_json(report, proof)
    return proof


def identity(proof):
    return tuple(proof[key] for key in ('sample_rate', 'channels', 'sample_count', 'sample_width', 'pcm_sha256'))


def clone(source, target, output_directory=None):
    """Copy encoded Polish audio, restore target markers, decode and verify; no TTS."""
    from .audio import validate_encoded
    if not compatible(source, target):
        raise ValueError('Duplicate performance context or target state differs')
    source_pcm, target_pcm = original_pcm(source), original_pcm(target)
    if identity(source_pcm) != identity(target_pcm):
        raise ValueError('Original audio differs; text similarity cannot authorize reuse')
    encoded = ROOT / source['wem_path']
    if sha256(encoded.read_bytes()) != source['wem_sha256']:
        raise ValueError('Source Polish WEM checksum changed')
    row = copy.deepcopy(target)
    proposed = {'text_en':row['text_en'], 'text_pl_tts':row['text_pl_tts'],
                'text_pl':row['audio_only_translation']['text_pl']}
    for key in ('text_en', 'text_pl_tts', 'display_segments', 'spoken_segments', 'timing_qa',
                'reviewed_duration_extension_seconds', 'duration_extension_reason'):
        if key in source:
            row[key] = copy.deepcopy(source[key])
    row['audio_only_translation']['text_pl'] = source['audio_only_translation']['text_pl']
    row['segments_pl'][0]['text'] = source['segments_pl'][0]['text']
    row['translation_source'] = 'agent_reviewed_identical_original_audio'
    output = Path(output_directory or ARTIFACTS / 'campaign/final') / (row['audio_hash_en'] + '.wem')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(encoded, output)
    validate_encoded(row, output)  # Replaces source labels with this original's markers.
    row.update(wem_path=str(output.relative_to(ROOT)), wem_sha256=sha256(output.read_bytes()),
               generation_status='reused_verified_polish_audio', qa_status='offline_final_wem_validated',
               install_status='awaiting_install')
    row.pop('qa_error', None)
    row['verified_audio_reuse'] = {'source_audio_hash':source['audio_hash_en'],
        'source_polish_wem_sha256':source['wem_sha256'], 'source_request_id':source.get('request_id'),
        'source_original_pcm':source_pcm, 'target_original_pcm':target_pcm,
        'prior_proposal':proposed, 'new_paid_requests':0,
        'decision':'Exact original mono PCM, same proven actor/casting and single zero cue. Transfer already reviewed Polish rendition consistently across identical originals; restore each target marker set and decode final WEM.'}
    return row
