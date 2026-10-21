"""Recipient-side mod installation from synthetic WEMs and source-bound DLGE deltas.

Uses the project's tested RPKG writer and journaled installer. No game manifest,
complete original dialogue resources, TTS account or build tools are distributed.
"""
import argparse
import base64
from collections import defaultdict
from contextlib import contextmanager
import json
import re
import shutil
from pathlib import Path
import struct
import sys
import zipfile
import zlib

from .common import save_json, sha256
from .archive import parse_tables, iter_entries, read_resource
from .patching import write_patch
from .installer import (checked_definition, fingerprints, partition_level, install,
                        rollback, closed_game, durable_bytes)


def make_delta(original, changed):
    """Copy matching original spans locally; ship only changed byte runs."""
    blocks = {}
    width = 16
    for at in range(max(0, len(original)-width+1)):
        blocks.setdefault(original[at:at+width], at)
    operations = []
    literal = bytearray()
    cursor = 0
    while cursor < len(changed):
        at = blocks.get(changed[cursor:cursor+width]) if cursor+width <= len(changed) else None
        if at is None:
            literal.append(changed[cursor])
            cursor += 1
            continue
        if literal:
            operations.append(['data', base64.b64encode(literal).decode('ascii')])
            literal.clear()
        count = width
        while at+count < len(original) and cursor+count < len(changed) and original[at+count] == changed[cursor+count]:
            count += 1
        operations.append(['copy', at, count])
        cursor += count
    if literal:
        operations.append(['data', base64.b64encode(literal).decode('ascii')])
    document = {'format': 1, 'original_sha256': sha256(original), 'final_sha256': sha256(changed),
                'final_size': len(changed), 'operations': operations}
    return zlib.compress(json.dumps(document, separators=(',', ':')).encode(), 9)


def apply_delta(original, delta):
    document = json.loads(zlib.decompress(delta))
    if document.get('format') != 1 or sha256(original) != document.get('original_sha256'):
        raise ValueError('Dialogue delta does not match the original game resource')
    expected = document['final_size']
    if not isinstance(expected, int) or not 0 <= expected <= 64*1024*1024:
        raise ValueError('Invalid dialogue delta size')
    result = bytearray()
    for operation in document['operations']:
        if operation[0] == 'copy' and len(operation) == 3:
            _, at, count = operation
            if not isinstance(at, int) or not isinstance(count, int) or at < 0 or count < 0 or at+count > len(original):
                raise ValueError('Dialogue copy span is outside original resource')
            result.extend(original[at:at+count])
        elif operation[0] == 'data' and len(operation) == 2:
            result.extend(base64.b64decode(operation[1], validate=True))
        else:
            raise ValueError('Invalid dialogue delta operation')
        if len(result) > expected:
            raise ValueError('Dialogue delta exceeds final size')
    if len(result) != expected or sha256(result) != document['final_sha256']:
        raise ValueError('Dialogue reconstruction checksum mismatch')
    return bytes(result)


def detect_games():
    import winreg
    steam = []
    for hive, key, name in ((winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
                            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')):
        try:
            with winreg.OpenKey(hive, key) as handle:
                steam.append(Path(winreg.QueryValueEx(handle, name)[0]))
        except OSError:
            pass
    libraries = set(steam)
    for root in steam:
        path = root/'steamapps/libraryfolders.vdf'
        if path.is_file():
            libraries.update(Path(value.replace('\\\\', '\\')) for value in re.findall(r'"path"\s+"([^"]+)"', path.read_text(encoding='utf-8')))
    games = []
    for library in libraries:
        manifest = library/'steamapps/appmanifest_3768760.acf'
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding='utf-8')
        directory = re.search(r'"installdir"\s+"([^"]+)"', text)
        if directory:
            candidate = (manifest.parent/'common'/directory[1]).resolve()
            if (candidate/'Runtime/packagedefinition.txt').is_file():
                games.append(candidate)
    return sorted(set(games))


def validate_package(archive):
    names = archive.namelist()
    if len(names) != len(set(names)) or 'release.json' not in names:
        raise ValueError('Duplicate entries or missing release manifest')
    document = json.loads(archive.read('release.json'))
    if document.get('format') != 1:
        raise ValueError('Unsupported release format')
    expected = {'release.json'}
    hashes = set()
    for item in document['resources']:
        if not re.fullmatch(r'[0-9A-F]{16}', item['hash']) or item['hash'] in hashes:
            raise ValueError('Invalid or duplicate resource hash')
        hashes.add(item['hash'])
        if item['type'] not in ('WWES', 'DLGE') or item['archive'] not in ('chunk0.rpkg', 'chunk1.rpkg'):
            raise ValueError('Unexpected release resource type or archive')
        extension = 'wem' if item['type'] == 'WWES' else 'delta'
        path = f"resources/{item['hash']}.{extension}"
        if item['path'] != path:
            raise ValueError('Release resource path escapes expected layout')
        expected.add(path)
    if set(names) != expected:
        raise ValueError('Unlisted files in release payload')
    return document


def build_recipient_bundle(package, game_root, stage):
    game_root = Path(game_root).resolve()
    with zipfile.ZipFile(package) as archive:
        document = validate_package(archive)
        if document['base_archive_fingerprints'] != fingerprints(game_root):
            raise ValueError('Nieobsługiwana wersja gry: archiwa różnią się od sprawdzonego buildu. Nie zmieniono gry.')
        _, _, plain = checked_definition((game_root/'Runtime/packagedefinition.txt').read_bytes())
        source_by_archive = defaultdict(dict)
        for item in document['resources']:
            source_by_archive[item['archive']][item['hash']] = item
        staged = []
        for name, wanted in sorted(source_by_archive.items()):
            source = game_root/'Runtime'/name
            chunk = int(name[5:-5])
            partition = document['partitions'][str(chunk)]
            _, level = partition_level(plain, partition)
            occupied = [int(m[1]) for p in (game_root/'Runtime').glob(f'chunk{chunk}patch*.rpkg')
                        if (m := re.fullmatch(r'chunk\d+patch(\d+)\.rpkg', p.name))]
            number = max([level, *occupied])+1
            if number > 255:
                raise ValueError('Brak wolnego obsługiwanego numeru patcha; nie nadpisano innych modów.')
            rows = []
            with source.open('rb') as stream:
                for h, kind, _, offset, flags, size, sysmem, vidmem, refs, *_ in iter_entries(source, parse_tables(source)):
                    item = wanted.pop(h, None)
                    if item is None:
                        continue
                    original = read_resource(stream, {'offset': offset, 'flags': flags, 'size': size})
                    if kind != item['type'] or sha256(original) != item['original_payload_sha256']:
                        raise ValueError('Oryginalny zasób gry nie zgadza się z paczką.')
                    content = archive.read(item['path'])
                    if sha256(content) != item['content_sha256']:
                        raise ValueError('Uszkodzone dane moda; pobierz paczkę ponownie.')
                    payload = apply_delta(original, content) if kind == 'DLGE' else content
                    if sha256(payload) != item['final_sha256']:
                        raise ValueError('Niezgodna suma kontrolna odtworzonego zasobu.')
                    rows.append({'hash': h, 'type': kind, 'payload': payload, 'refs': refs, 'sysmem': sysmem, 'vidmem': vidmem})
            if wanted:
                raise ValueError('Brakuje oryginalnych zasobów wymaganych przez mod.')
            path = stage/f'chunk{chunk}patch{number}.rpkg'
            validation = write_patch(path, rows, chunk, number)
            with path.open('r+b') as stream:
                import os
                os.fsync(stream.fileno())
            staged.append({'filename': path.name, 'sha256': sha256(path.read_bytes()), 'chunk': chunk,
                           'patch_number': number, 'partition': partition, 'validation': validation})
            print(f'Przygotowano {path.name}: {len(rows)} zasobów.', flush=True)
        bundle = {'status': 'offline_validated', 'patches': staged, 'resources': document['resources'],
                  'base_archive_fingerprints': document['base_archive_fingerprints']}
        save_json(stage/'bundle.json', bundle)
    return stage/'bundle.json'


@contextmanager
def operation_lock(state):
    import msvcrt
    state.mkdir(parents=True, exist_ok=True)
    with (state/'operation.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'1')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Trwa inna instalacja lub cofanie moda. Poczekaj na zakończenie.') from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def main(argv=None, *, _lock_held=False):
    parser = argparse.ArgumentParser(description='007 First Light — Polski dubbing, Kris Krej')
    parser.add_argument('command', choices=('install', 'dry-run', 'rollback', 'verify'))
    parser.add_argument('--game-root', type=Path)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--install-id')
    parser.add_argument('--all', action='store_true', help='Cofnij wszystkie instalacje tego instalatora')
    args = parser.parse_args(argv)
    package = args.package or Path(sys.executable if getattr(sys, 'frozen', False) else __file__).resolve().parent/'mod-data.zip'
    if args.command == 'verify':
        with zipfile.ZipFile(package) as archive:
            document = validate_package(archive)
            for item in document['resources']:
                if sha256(archive.read(item['path'])) != item['content_sha256']:
                    raise ValueError('Uszkodzony plik paczki: '+item['path'])
        print(f"Paczka poprawna: {len(document['resources'])} zasobów, wersja {document['version']}.")
        return 0
    games = [args.game_root.resolve()] if args.game_root else detect_games()
    if len(games) != 1:
        raise ValueError('Nie znaleziono jednej instalacji Steam. Uruchom z --game-root "folder gry".')
    game = games[0]
    state = game/'.007pl'
    if not _lock_held:
        with operation_lock(state):
            return main(argv, _lock_held=True)
    backup = state/'backups'
    if args.command == 'rollback':
        candidates = sorted(backup.glob('*/journal.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        identifier = args.install_id or next((json.loads(p.read_text(encoding='utf-8'))['install_id'] for p in candidates
                                             if json.loads(p.read_text(encoding='utf-8'))['status'] != 'rolled_back'), None)
        if identifier is None:
            print('Nie ma instalacji tego wydania do cofnięcia.')
            return 0
        result = rollback(identifier, backup_root=backup, expected_game_root=game)
        print('Przywrócono stan sprzed instalacji. ID: '+result['install_id'])
        if args.all:
            return main(['rollback', '--game-root', str(game), '--all'], _lock_held=True)
        return 0
    closed_game()
    for journal in backup.glob('*/journal.json'):
        if json.loads(journal.read_text(encoding='utf-8'))['status'] not in ('installed', 'rolled_back'):
            raise ValueError('Wykryto przerwaną instalację. Najpierw uruchom USUN.cmd, aby ją bezpiecznie cofnąć.')
    package_sha256 = sha256(package.read_bytes())
    for journal_path in backup.glob('*/journal.json'):
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        if journal['status'] == 'installed' and journal.get('release_payload_sha256') == package_sha256:
            if fingerprints(game) != journal['base_archive_fingerprints']:
                raise ValueError('Gra zmieniła się od instalacji; wymagany przegląd zgodności.')
            _, _, plain = checked_definition((game/'Runtime/packagedefinition.txt').read_bytes())
            for item in journal['patches']:
                if sha256((game/'Runtime'/item['filename']).read_bytes()) != item['sha256']:
                    raise ValueError('Plik zainstalowanego moda zmienił się. Nie nadpisano zmian.')
            if all(partition_level(plain, change['partition'])[1] >= change['after'] for change in journal['changes']):
                print('Ta wersja polskiego dubbingu jest już zainstalowana.')
                return 0
    import uuid
    stage = state/'staging'/uuid.uuid4().hex
    stage.mkdir(parents=True, exist_ok=False)
    bundle = build_recipient_bundle(package, game, stage)
    result = install(bundle, dry_run=args.command == 'dry-run', game_root=game, backup_root=backup)
    if args.command == 'install':
        result['release_payload_sha256'] = package_sha256
        save_json(backup/result['install_id']/'journal.json', result)
    print('Plan poprawny — gra bez zmian.' if args.command == 'dry-run' else 'Zainstalowano polski dubbing. Audio: English; napisy: Polski. ID: '+result['install_id'])
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('BŁĄD: '+str(error), file=sys.stderr)
        raise SystemExit(1)
