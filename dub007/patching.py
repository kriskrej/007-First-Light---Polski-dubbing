"""Raw WWES patch writer adapted from toolbox's write_patch implementation."""
import struct

from .archive import iter_entries, parse_tables, read_resource


def write_patch(path, resources, chunk, patch):
    if not (0 <= chunk <= 255 and 1 <= patch <= 255):
        raise ValueError("Patch and chunk IDs do not fit the verified subheader")
    if path.name != f"chunk{chunk}patch{patch}.rpkg":
        raise ValueError("Patch name and header must agree")
    if len({row["hash"] for row in resources}) != len(resources):
        raise ValueError("Duplicate hash in patch")
    info = b"".join(row["type"].encode("ascii")[::-1] +
                    struct.pack("<IIII", len(row["refs"]), len(row["payload"]), row["sysmem"], row["vidmem"]) + row["refs"]
                    for row in resources)
    header = b"2KPR" + bytes([1, 0, 0, 0, chunk, 0, patch, 0x78, 0x78])
    header += struct.pack("<IIII", len(resources), 20 * len(resources), len(info), 0)
    offset = len(header) + 20 * len(resources) + len(info)
    hashes = []
    for row in resources:
        hashes.append(struct.pack("<QQI", int(row["hash"], 16), offset, 0))
        offset += len(row["payload"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"".join(hashes) + info + b"".join(row["payload"] for row in resources))
    entries = list(iter_entries(path, parse_tables(path)))
    with path.open("rb") as stream:
        for expected, actual in zip(resources, entries):
            h, resource_type, index, position, flags, size, sysmem, vidmem, references, _, _ = actual
            decoded = read_resource(stream, {"offset": position, "flags": flags, "size": size})
            if (h, resource_type, references, sysmem, vidmem, decoded) != (
                    expected["hash"], expected["type"], expected["refs"], expected["sysmem"], expected["vidmem"], expected["payload"]):
                raise ValueError("Patch resource readback failed")
    return {"resources": len(entries), "payloads_and_metadata_readback_verified": True}
