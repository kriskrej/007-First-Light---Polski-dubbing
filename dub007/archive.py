"""Validated RPKG v2 index and cached resource reads, adapted from toolbox.

Provenance: research/toolbox/tools/rpkg_index.py and rpkg_read.py.
Only archive tables are indexed; game archives are always opened read-only.
"""
from collections import Counter
import json
import re
import sqlite3
import struct

import lz4.block

from .common import ROOT, cache_root, save_json, sha256, upstream

KEY = bytes([0xDC, 0x45, 0xA6, 0x9C, 0xD3, 0x72, 0x4C, 0xAB])
NAME = re.compile(r"chunk(\d+)(?:patch(\d+))?\.rpkg", re.IGNORECASE)


def parse_tables(path):
    match = NAME.fullmatch(path.name)
    if not match:
        raise ValueError(f"Unsupported archive filename: {path.name}")
    chunk, patch = int(match[1]), int(match[2] or 0)
    if chunk > 255 or patch > 255:
        raise ValueError("Chunk and patch IDs must fit in one byte")
    with path.open("rb") as stream:
        header = stream.read(25)
        if len(header) != 25 or header[:4] != b"2KPR":
            raise ValueError("Unsupported RPKG header")
        if header[8] != chunk or header[10] != patch:
            raise ValueError("Archive filename and subheader disagree")
        count, hash_size, info_size = struct.unpack_from("<III", header, 13)
        if hash_size != count * 20:
            raise ValueError("Invalid hash table size")
        deleted = []
        if patch:
            raw_count = stream.read(4)
            if len(raw_count) != 4:
                raise ValueError("Truncated patch deletion list")
            deletion_count = struct.unpack("<I", raw_count)[0]
            if deletion_count > 10_000_000:
                raise ValueError("Invalid deletion count")
            raw_deleted = stream.read(deletion_count * 8)
            if len(raw_deleted) != deletion_count * 8:
                raise ValueError("Truncated deletion list")
            deleted = [f"{item[0]:016X}" for item in struct.iter_unpack("<Q", raw_deleted)]
        table_start = stream.tell()
        if table_start + hash_size + info_size > path.stat().st_size:
            raise ValueError("Index extends past archive end")
        hashes, info = stream.read(hash_size), stream.read(info_size)
    fingerprint = {
        "size": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "index_sha256": sha256(header + struct.pack("<I", len(deleted)) +
                               b"".join(struct.pack("<Q", int(h, 16)) for h in deleted) + hashes + info),
        "fingerprint_scope": "header, deletion list and complete index; not complete payload area",
    }
    return header, hashes, info, table_start, deleted, fingerprint


def iter_entries(path, tables):
    header, hashes, info, start, deleted, fingerprint = tables
    position = 0
    data_start = start + len(hashes) + len(info)
    for index, (hash_value, offset, flags) in enumerate(struct.iter_unpack("<QQI", hashes)):
        if position + 20 > len(info):
            raise ValueError("Truncated resource metadata")
        resource_type = info[position:position + 4][::-1].decode("ascii")
        reference_size, size, sysmem, vidmem = struct.unpack_from("<IIII", info, position + 4)
        record_offset = start + len(hashes) + position
        position += 20
        references = info[position:position + reference_size]
        position += reference_size
        if len(references) != reference_size:
            raise ValueError("Truncated reference table")
        if reference_size:
            if reference_size < 4:
                raise ValueError("Reference table too short")
            reference_count = struct.unpack_from("<I", references)[0] & 0x3FFFFFFF
            if 4 + reference_count * 9 > reference_size:
                raise ValueError("Invalid reference count")
        compressed_size = flags & 0x3FFFFFFF
        stored_size = compressed_size or size
        if offset < data_start or offset + stored_size > fingerprint["size"]:
            raise ValueError(f"Resource {hash_value:016X} extends outside payload area")
        yield (f"{hash_value:016X}", resource_type, index, offset, flags,
               size, sysmem, vidmem, references, start + 20 * index, record_offset)
    if position != len(info):
        raise ValueError("Unparsed resource metadata remains")


def active_archives(baseline=False):
    paths = sorted((ROOT / "Runtime").glob("*.rpkg"))
    # Base-only build is fully understood. Patch precedence must be read from
    # packagedefinition before enabling patched builds, never guessed by filename.
    patches = [path for path in paths if "patch" in path.name.casefold()]
    if patches:
        if not baseline:
            raise RuntimeError("Patched installation requires verified packagedefinition precedence; refusing to guess")
        from .deployment import active_install
        installed = active_install()
        allowed = {item["filename"]: item["sha256"] for item in installed["patches"]} if installed else {}
        if any(path.name not in allowed or sha256(path.read_bytes()) != allowed[path.name] for path in patches):
            raise RuntimeError("Unknown override detected; original-baseline audit refuses an unverified mod")
        paths = [path for path in paths if path not in patches]
    expected = {"chunk0.rpkg", "chunk1.rpkg"}
    if {path.name for path in paths} != expected:
        raise RuntimeError("Unsupported partition layout; inspect packagedefinition first")
    return paths


def open_index():
    database = sqlite3.connect(cache_root() / "index-v1.sqlite3")
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA journal_mode=WAL")
    database.execute("""CREATE TABLE IF NOT EXISTS archives
        (name TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, header BLOB NOT NULL)""")
    database.execute("""CREATE TABLE IF NOT EXISTS resources
        (archive TEXT, hash TEXT, type TEXT, resource_index INTEGER, offset INTEGER,
         flags INTEGER, size INTEGER, sysmem INTEGER, vidmem INTEGER, refs BLOB,
         hash_record INTEGER, info_record INTEGER, PRIMARY KEY(archive, hash))""")
    # Hash-only dialogue/dependency lookups cannot use the archive-first key.
    # Create once in the existing cache, without re-reading game archives.
    database.execute("CREATE INDEX IF NOT EXISTS resources_hash ON resources(hash)")
    return database


def ensure_index(baseline=False):
    database = open_index()
    inventory = {}
    paths = active_archives(baseline)
    for path in paths:
        tables = parse_tables(path)
        fingerprint = tables[-1]
        stored = database.execute("SELECT fingerprint FROM archives WHERE name=?", (path.name,)).fetchone()
        rebuilt = stored is None or json.loads(stored[0]) != fingerprint
        if rebuilt:
            print(f"Indexing {path.name} tables…", flush=True)
            with database:
                database.execute("DELETE FROM resources WHERE archive=?", (path.name,))
                database.executemany("INSERT INTO resources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                     ((path.name, *entry) for entry in iter_entries(path, tables)))
                database.execute("INSERT OR REPLACE INTO archives VALUES (?,?,?)",
                                 (path.name, json.dumps(fingerprint), tables[0]))
        counts = dict(database.execute("SELECT type,count(*) FROM resources WHERE archive=? GROUP BY type",
                                       (path.name,)).fetchall())
        inventory[path.name] = {**fingerprint, "resource_types": counts, "cache_rebuilt": rebuilt}
    names = [path.name for path in paths]
    marks = ",".join("?" for _ in names)
    with database:
        database.execute(f"DELETE FROM resources WHERE archive NOT IN ({marks})", names)
        database.execute(f"DELETE FROM archives WHERE name NOT IN ({marks})", names)
        database.execute("CREATE INDEX IF NOT EXISTS resources_type ON resources(type)")
    save_json(ROOT / "docs" / "INVENTORY.json", inventory)
    return database, inventory


def read_resource(stream, entry):
    stream.seek(entry["offset"])
    compressed_size = entry["flags"] & 0x3FFFFFFF
    expected = compressed_size or entry["size"]
    data = stream.read(expected)
    if len(data) != expected:
        raise ValueError("Truncated resource payload")
    if entry["flags"] & 0x80000000:
        data = bytes(value ^ KEY[index & 7] for index, value in enumerate(data))
    if compressed_size:
        data = lz4.block.decompress(data, uncompressed_size=entry["size"])
    if len(data) != entry["size"]:
        raise ValueError("Unexpected decoded resource size")
    return data


def refs(entry):
    raw = entry["refs"]
    if not raw:
        return []
    count = struct.unpack_from("<I", raw)[0] & 0x3FFFFFFF
    return [{"flag": raw[4 + index], "hash": f"{struct.unpack_from('<Q', raw, 4 + count + 8 * index)[0]:016X}"}
            for index in range(count)]
