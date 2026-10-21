"""Build a recipient payload from an already validated local campaign bundle."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dub007.archive import parse_tables, iter_entries, read_resource
from dub007.common import sha256
from dub007.installer import fingerprints
from dub007.release import make_delta, apply_delta, validate_package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--game-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text(encoding='utf-8'))
    if bundle.get('status') != 'offline_validated' or bundle['base_archive_fingerprints'] != fingerprints(args.game_root):
        raise ValueError('Bundle or game does not match the validated original build')
    if args.output.exists():
        raise ValueError('Output already exists; released versions are immutable')
    originals = {}
    expected = {r['hash']: r for r in bundle['resources']}
    for name in {r['archive'] for r in expected.values()}:
        path = args.game_root/'Runtime'/name
        wanted = {r['hash'] for r in expected.values() if r['archive'] == name and r['type'] == 'DLGE'}
        with path.open('rb') as stream:
            for h, kind, _, offset, flags, size, *_ in iter_entries(path, parse_tables(path)):
                if h in wanted:
                    originals[h] = read_resource(stream, {'offset':offset,'flags':flags,'size':size})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for patch in bundle['patches']:
            path = args.bundle.parent/patch['filename']
            if sha256(path.read_bytes()) != patch['sha256']:
                raise ValueError('Patch checksum mismatch')
            with path.open('rb') as stream:
                for h, kind, _, offset, flags, size, *_ in iter_entries(path, parse_tables(path)):
                    source = expected[h]
                    final = read_resource(stream, {'offset':offset,'flags':flags,'size':size})
                    if kind == 'DLGE':
                        original = originals[h]
                        if sha256(original) != source['original_payload_sha256']:
                            raise ValueError('Original DLGE checksum mismatch')
                        content, suffix = make_delta(original, final), 'delta'
                        if apply_delta(original, content) != final:
                            raise ValueError('Delta reconstruction failed')
                    elif kind == 'WWES':
                        content, suffix = final, 'wem'
                    else:
                        raise ValueError('Unexpected release resource')
                    name = f'resources/{h}.{suffix}'
                    output.writestr(name, content)
                    entries.append({**source,'path':name,'content_sha256':sha256(content),'final_sha256':sha256(final)})
        if {r['hash'] for r in entries} != set(expected):
            raise ValueError('Not all bundle resources were packaged')
        document = {'format':1,'version':args.version,'created_utc':datetime.now(timezone.utc).isoformat(),
                    'author':'Kris Krej','resources':entries,'base_archive_fingerprints':bundle['base_archive_fingerprints'],
                    'partitions':{str(p['chunk']):p['partition'] for p in bundle['patches']},
                    'polish_audio_resources':bundle['audio_lines'],'original_full_dlge_included':False,
                    'packagedefinition_included':False,'source_bundle_signature':bundle['signature'],
                    'distribution_clearance':'Requires separate game/audio rights review before publication.'}
        output.writestr('release.json', json.dumps(document, ensure_ascii=False, separators=(',',':')))
    with zipfile.ZipFile(args.output) as output:
        validate_package(output)
        for item in entries:
            if sha256(output.read(item['path'])) != item['content_sha256']:
                raise ValueError('Release payload readback failed')
    print(f'Validated recipient package: {len(entries)} resources, {args.output.stat().st_size} bytes')
    print('SHA256: '+sha256(args.output.read_bytes()))


if __name__ == '__main__':
    main()
