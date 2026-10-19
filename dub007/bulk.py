"""Bounded first-pass pipeline: one paid request and one local encoder at a time.

The existing TTS lock, included-credit checks and durable per-request journal stay
in use. A single prefetched result overlaps network waiting with local fitting;
neither timing failures nor uncertain requests trigger automatic paid retries.
"""
from concurrent.futures import ThreadPoolExecutor
import json

from .campaign import database, ensure_chapter, original_wav, progress, save_row
from .common import ARTIFACTS, ROOT, sha256


def generate_one(audio_hash):
    from .tts import generate
    store = database()
    try:
        found = store.database.execute('SELECT document FROM resources WHERE audio_hash=?', (audio_hash,)).fetchone()
        if found is None:
            raise ValueError('Unknown first-pass resource')
        row = json.loads(found[0])
        if row['qa_status'] != 'original_metadata_verified':
            raise ValueError('First-pass resource no longer has a reviewed script')
        entries = store.load(audio_hash)['entries']
        if entries and row.get('generation_status') != 'generated':
            raise RuntimeError('First-pass resource already has a request; review its journal before continuing')
        original_wav(row)
        cues = row.get('original_markers', {}).get('cue ', {}).get('points', [])
        if row.get('chapter_id') and cues and all(point['sample_offset'] == 0 for point in cues) and 'smpl' not in row['original_markers']:
            row.setdefault('available_tail_extension_seconds', .25)
            if len(row['segments_pl']) == 1:
                row.setdefault('edge_padding_reduction_seconds', .015)
        result = generate(row, store=store, casting_path=ROOT / 'config/campaign-casting.json', with_timestamps=True)
        row.update(generation_status='generated', generated_audio_path=result['audio_path'],
                   request_id=result.get('request_id'), tts_cache_key=result['cache_key'],
                   alignment_path=result.get('alignment_path'))
        save_row(store, row)
        return row
    finally:
        store.close()


def finish_one(row):
    from .audio import prepare_campaign, encode
    from .campaign import compatible_retake
    from .timing import prepare_campaign_segments
    from .shared_audio import prepare_shared

    store = database()
    try:
        current = json.loads(store.database.execute('SELECT document FROM resources WHERE audio_hash=?',
                                                   (row['audio_hash_en'],)).fetchone()[0])
        if any(current.get(field) != row.get(field) for field in ('tts_cache_key', 'text_pl_tts', 'voice_id', 'qa_status')):
            raise RuntimeError('Resource changed while waiting for local fitting; generated take retained')
        entries = store.load(row['audio_hash_en'])['entries']
        result = next(e for e in entries if e['cache_key'] == row['tts_cache_key'] and e['status'] == 'succeeded')
        if sha256((ROOT / result['audio_path']).read_bytes()) != result['audio_sha256']:
            raise ValueError('Generated audio checksum changed')
        alternatives = [e for e in entries if e['status'] == 'succeeded' and e.get('alignment_path')
                        and e['cache_key'] != row['tts_cache_key']
                        and e['identity']['voice_id'] == row['voice_id']
                        and e['identity']['request']['model_id'] == row['model_id']
                        and e['identity']['request']['voice_settings'] == row['settings']
                        and compatible_retake(e, result)]
        try:
            source = ROOT / row['generated_audio_path']
            if row.get('channels', 1) != 1:
                from .surround import prepare
                prepared = prepare(row, source, alternatives)
            elif len(row['segments_pl']) > 1:
                if not row.get('alignment_path'):
                    raise ValueError('Multi-segment speech requires alignment; no paid retry')
                prepared = prepare_campaign_segments(row, source, cached_takes=alternatives)
            else:
                try:
                    prepared = prepare_campaign(row, source)
                except ValueError as error:
                    prepared = prepare_shared(row, source, error)
            final = encode(row, prepared, ARTIFACTS / 'campaign/final')
            row.update(prepared_audio_path=str(prepared.relative_to(ROOT)), wem_path=str(final.relative_to(ROOT)),
                       wem_sha256=sha256(final.read_bytes()), qa_status='offline_final_wem_validated', install_status='awaiting_install')
            row.pop('qa_error', None)
        except ValueError as error:
            row.update(qa_status='needs_agent_adaptation', qa_error=str(error))
        save_row(store, row)
        return {'hash':row['audio_hash_en'], 'scope':row.get('chapter_id'),
                'status':row['qa_status'], 'error':row.get('qa_error')}
    finally:
        store.close()


def pipeline(hashes, *, produce=generate_one, consume=finish_one):
    """Keep at most one next request in flight and drain it on any exception."""
    remaining = iter(dict.fromkeys(hashes))
    first = next(remaining, None)
    if first is None:
        return
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='dub007-tts') as executor:
        future = executor.submit(produce, first)
        while future is not None:
            row = future.result()
            following = next(remaining, None)
            future = executor.submit(produce, following) if following is not None else None
            yield consume(row)


def run(chapter):
    store = database()
    try:
        ensure_chapter(store, chapter)
        rows = [json.loads(d) for d, in store.database.execute(
            'SELECT r.document FROM chapter_resources c JOIN resources r ON c.audio_hash=r.audio_hash WHERE c.chapter=? ORDER BY c.ordinal',
            (chapter,))]
        hashes = [r['audio_hash_en'] for r in rows if r['qa_status'] == 'original_metadata_verified'
                  and r.get('generation_status') in ('not_started', 'generated')]
    finally:
        store.close()
    for result in pipeline(hashes):
        print(json.dumps(result, ensure_ascii=False), flush=True)
    store = database()
    try:
        report = progress(store, chapter)
        report.update(first_pass_complete=True, automatically_purchased_retakes=0,
                      remaining_adaptations=store.database.execute(
                          "SELECT COUNT(*) FROM resources r JOIN chapter_resources c ON c.audio_hash=r.audio_hash WHERE c.chapter=? AND r.status='needs_agent_adaptation'", (chapter,)).fetchone()[0])
        return report
    finally:
        store.close()
