"""Bounded offline fitting of proven shared dialogue, with no TTS requests."""
import copy
import json

from .common import ROOT, sha256
from .audio import prepare, has_one_polish_segment


def prepare_shared(row, generated, original_error):
    policy = json.loads((ROOT / 'config/campaign.json').read_text(encoding='utf-8')).get('shared_audio_fitting', {})
    scope = row.get('reactive_scope') or row.get('native_library_scope')
    message = str(original_error)
    timing_error = ('needs shorter text or retake' in message or message in (
        'Speech would exceed final sample count', 'Stretch duration exceeded allowed 30-ms rounding margin'))
    markers = row.get('original_markers', {})
    cues = markers.get('cue ', {}).get('points', [])
    if not (policy.get('enabled') is True and scope in policy.get('reviewed_scopes', [])
            and policy.get('maximum_extension_seconds') == .2 and timing_error
            and row.get('resource_type') == 'WWES' and row.get('channels') == 1
            and row.get('audio_mapping_status') == 'reference_verified'
            and len(row.get('original_payload_sha256', '')) == 64
            and row.get('chapter_id') and has_one_polish_segment(row)
            and len(cues) == 1 and cues[0]['sample_offset'] == 0 and 'smpl' not in markers):
        raise original_error
    configuration = json.loads((ROOT / 'config/chapters.json').read_text(encoding='utf-8'))['chapters'].get(scope, {})
    if row.get('reactive_scope'):
        if configuration.get('scope_kind') != 'reactive':
            raise original_error
        from .reactive import scene_key
    else:
        from .native import library_scene_key as scene_key
    scene_key(row, configuration)  # Recheck the exact original mapping/label evidence.
    source_sha256 = sha256(generated.read_bytes())
    resampled_audio = None
    for centiseconds in range(1, 21):
        for quiet_guard in (.015, .02, .011):
            candidate = copy.deepcopy(row)
            candidate.update(reviewed_duration_extension_seconds=centiseconds / 100,
                duration_extension_reason='Reviewed shared-dialogue policy: minimum successful <=200ms, one zero cue, no loops, full paid speech retained, <=1.15 tempo, actual >=10ms quiet.',
                available_onset_shift_seconds=.1, available_tail_extension_seconds=.5,
                minimum_tail_seconds=quiet_guard,
                reviewed_existing_tail_policy='Use measured existing quiet only; original onset shift<=100ms, existing tail<=500ms.')
            try:
                result = prepare(candidate, generated, resampled_audio=resampled_audio)
            except ValueError:
                continue
            finally:
                # Reuse only the conversion produced by this invocation for
                # this immutable paid take, never a cache from another request.
                converted = generated.parent / 'resampled.wav'
                if converted.is_file():
                    resampled_audio = converted
            if candidate['timing_qa']['tail_seconds'] < .01:
                continue
            if sha256(generated.read_bytes()) != source_sha256:
                raise ValueError('Paid audio changed during offline fitting')
            candidate['shared_fit_review'] = {
                'scope': scope, 'new_paid_requests': 0,
                'source_sha256': source_sha256,
                'extension_seconds': centiseconds / 100,
                'full_speech_retained': True, 'internal_cues_or_loops': False,
                'tempo_limit': 1.15, 'minimum_measured_quiet_seconds': .01}
            row.update(candidate)
            return result
    raise original_error
