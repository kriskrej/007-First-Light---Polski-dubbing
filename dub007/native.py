"""Recover chapter membership from native audio labels and proven DLGE refs."""
from contextlib import ExitStack
import json
import re

from .archive import open_index, parse_tables, read_resource
from .common import ARTIFACTS, ROOT, save_json, sha256
from .selection import native_labels, riff_metadata


def library_scene_key(row, configuration):
    """Sort a proven shared library without inventing numbered story scenes."""
    expected = configuration.get("reviewed_native_labels", {}).get(row["audio_hash_en"])
    if (configuration.get("scope_kind") != "native_library" or not expected
            or row.get("native_audio_label") != expected
            or row["line_id"] not in configuration.get("source_dlge_hashes", [])):
        raise ValueError("Native library resource is outside its reviewed evidence")
    match = re.fullmatch(r"(.+)_" + re.escape(row["speaker_id"].lower())
                         + r"_(\d{3})(?:_alt\d+)?(?:_(?:combat|regular|stealth)_?)?", expected)
    if not match:
        raise ValueError("Unreviewed native library label")
    return 0, match[1], int(match[2]), row["audio_hash_en"]


def validate_library_owners(index, configuration):
    from .archive import refs
    chains = configuration.get('source_reference_chains')
    if chains is not None:
        required = set(configuration.get('source_dlge_hashes', []))
        if not required or not isinstance(chains, list) or not chains:
            raise ValueError('Native library requires explicit resource reference chains')
        covered, owners = set(), set()
        for chain in chains:
            if (not isinstance(chain, list) or len(chain) not in (2, 3)
                    or any(not isinstance(node, dict) or not node.get('hash') for node in chain)
                    or [node.get('type') for node in chain] not in
                    (['TEMP', 'DLGE'], ['TEMP', 'SDEF', 'DLGE'])
                    or len({node['hash'] for node in chain}) != len(chain)
                    or chain[-1]['hash'] not in required):
                raise ValueError('Unsupported native library reference chain')
            entries = []
            for node in chain:
                matches = index.execute('SELECT * FROM resources WHERE hash=? AND type=?',
                                        (node['hash'], node['type'])).fetchall()
                if len(matches) != 1:
                    raise ValueError('Native reference chain resource is missing or ambiguous')
                entries.append(matches[0])
            for parent, child in zip(entries, chain[1:]):
                if child['hash'] not in {ref['hash'] for ref in refs(parent)}:
                    raise ValueError('Native resource reference chain has changed')
            covered.add(chain[-1]['hash'])
            owners.add(chain[0]['hash'])
        if covered != required:
            raise ValueError('Native reference chains do not cover all reviewed dialogue')
        return sorted(owners)
    owners = configuration.get('owner_temp_hashes') or [configuration.get('owner_temp_hash')]
    if not owners or any(not h for h in owners) or len(set(owners)) != len(owners):
        raise ValueError('Native library requires explicit unique TEMP owners')
    referenced = set()
    for h in owners:
        entries = index.execute("SELECT * FROM resources WHERE hash=? AND type='TEMP'", (h,)).fetchall()
        if len(entries) != 1:
            raise ValueError('Native library TEMP owner is missing or ambiguous')
        referenced.update(r['hash'] for r in refs(entries[0]))
    required = set(configuration.get('source_dlge_hashes', []))
    if not required or not required.issubset(referenced):
        raise ValueError('Shared library owners no longer reference all reviewed dialogue')
    return owners


def complete_library_manifest(prepared, scope, configuration):
    rows = prepared.get('rows', [])
    if not rows or any(r.get('native_library_scope') != scope for r in rows):
        return False
    if {r['audio_hash_en'] for r in rows} != set(configuration.get('reviewed_native_labels', {})):
        return False
    for row in rows:
        library_scene_key(row, configuration)
    return True


def prepare_library(scope):
    from .archive import refs
    from .campaign import chapter_config
    from .mission import run as prepare_mission

    configuration = chapter_config()["chapters"].get(scope, {})
    if configuration.get("scope_kind") != "native_library":
        raise ValueError("Unknown reviewed native library")
    manifest = ARTIFACTS / "chapters" / scope / "manifest.json"
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if complete_library_manifest(previous, scope, configuration):
            return {"status": "prepared_manifest_reused", "scope": scope,
                    "resources": len(previous['rows'])}
    index = open_index()
    evidence = {}
    try:
        owner_hashes = validate_library_owners(index, configuration)
        with ExitStack() as stack:
            streams = {}
            for h, label in configuration["reviewed_native_labels"].items():
                entries = index.execute("SELECT * FROM resources WHERE hash=? AND type='WWES'", (h,)).fetchall()
                if len(entries) != 1:
                    raise ValueError("Ambiguous native library audio")
                entry = entries[0]
                if entry["archive"] not in streams:
                    streams[entry["archive"]] = stack.enter_context((ROOT / "Runtime" / entry["archive"]).open("rb"))
                raw = read_resource(streams[entry["archive"]], entry)
                labels = {item["label"] for item in native_labels(riff_metadata(raw)["original_markers"])}
                if labels != {label}:
                    raise ValueError("Native library filename labels changed")
                evidence[h] = {"label": label, "original_payload_sha256": sha256(raw), "archive": entry["archive"]}
    finally:
        index.close()
    report = prepare_mission(configuration["mission_folder"], scope, native_labels=evidence, native_only=True)
    prepared = json.loads(manifest.read_text(encoding="utf-8"))
    if {r["audio_hash_en"] for r in prepared["rows"]} != set(evidence):
        raise ValueError("Native library does not match the reviewed hash set")
    for row in prepared["rows"]:
        row.update(native_library_scope=scope, processing_order="library_label_not_story_chronology")
        library_scene_key(row, configuration)
    save_json(manifest, prepared)
    save_json(ROOT / "docs/chapters" / f"{scope}-mapping.json", {
        "scope": scope, "owner_temp_hash": configuration.get("owner_temp_hash"),
        "owner_temp_hashes": owner_hashes,
        "source_dlge_hashes": configuration["source_dlge_hashes"], "selection": evidence,
        "ordering": "Native library label order, no numbered chapter or scene assigned",
        "runtime_verified": False})
    return report


def audit_scene_range(first, last, destination):
    if not 0 <= first < last:
        raise ValueError("Invalid native scene range")
    audio_paths = {r["hash"]: r["path"] for r in json.loads((ROOT / "research/bond-hashes/paths/WWES.json").read_text(encoding="utf-8"))}
    dialogue_paths = {r["hash"]: r["path"] for r in json.loads((ROOT / "research/bond-hashes/paths/DLGE.json").read_text(encoding="utf-8"))}
    candidates = set()
    with (ARTIFACTS / "texts/dialogue-mapped.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            h = row.get("audio_hash_en")
            if h and row.get("text_en") and (not audio_paths.get(h) or not dialogue_paths.get(row["line_id"])):
                candidates.add(h)
    selected = {}
    database = open_index()
    try:
        with ExitStack() as stack:
            streams = {}
            for archive, fingerprint in database.execute("SELECT name,fingerprint FROM archives"):
                if parse_tables(ROOT / "Runtime" / archive)[-1] != json.loads(fingerprint):
                    raise ValueError("Source archive changed since native audit index")
                streams[archive] = stack.enter_context((ROOT / "Runtime" / archive).open("rb"))
            for h in sorted(candidates):
                entries = database.execute("SELECT * FROM resources WHERE hash=? AND type='WWES'", (h,)).fetchall()
                if len(entries) != 1:
                    raise ValueError("Ambiguous native audio placement")
                entry = entries[0]
                payload = read_resource(streams[entry["archive"]], entry)
                labels = native_labels(riff_metadata(payload)["original_markers"])
                matches = [r["label"] for r in labels if (m := re.match(r"[as](\d+)_", r["label"])) and first <= int(m[1]) < last]
                if matches:
                    if len(set(matches)) != 1:
                        raise ValueError("Multiple native scene labels require agent review")
                    selected[h] = {"label": matches[0], "original_payload_sha256": sha256(payload), "archive": entry["archive"]}
    finally:
        database.close()
    save_json(destination, {"scene_range": [first, last], "selection": selected,
                            "evidence": "Native WEM LIST labels; audio hashes retain direct local EN DLGE references.",
                            "scope": "Text-bearing assets whose public audio or dialogue paths are empty."})
    return selected
