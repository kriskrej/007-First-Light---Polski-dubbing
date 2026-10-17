"""First Light DLGE reader verified against current binary records.

Container concepts: TonyTools Languages.cpp; WavFile layout differs.
The toolkit's fixed offsets 27/3/4 are reference indices, not section tags.
English/Polish locale indices 1/9 were identified in decrypted samples.
"""
from functools import lru_cache
import re
import struct

from .common import upstream

LANGUAGES = ("source", "en", "fr", "it", "de", "es", "ru", "es-MX", "pt-BR",
             "pl", "zh-CN", "ja", "zh-TW", "ko", "tr", "uk")
FIELDS = re.compile(r"//(.*?)\\\\")
TIMING = re.compile(r"//\(([^)]*)\)\\\\")


@lru_cache(maxsize=1)
def upstream_crypto():
    return upstream("glacier.rpkg")


@lru_cache(maxsize=200_000)
def decrypt(ciphertext):
    return upstream_crypto().xtea_decrypt(ciphertext).split(b"\0", 1)[0].decode("utf-8")


def text_fields(text):
    fields = [match.group(1) for match in FIELDS.finditer(text)]
    timings = list(TIMING.finditer(text))
    segments = []
    for index, timing in enumerate(timings):
        end = timings[index + 1].start() if index + 1 < len(timings) else len(text)
        segments.append({"segment_id": index, "timing_raw": timing.group(1), "text": text[timing.end():end]})
    return {"speaker_id": fields[0].strip("[]") if fields else None,
            "speaker_name": fields[1].strip("{}") if len(fields) > 1 else None,
            "fields_raw": fields, "segments": segments}


class Reader:
    def __init__(self, data):
        self.data = data
        self.position = 0

    def read(self, count):
        if count < 0 or self.position + count > len(self.data):
            raise ValueError(f"DLGE read outside payload at {self.position}")
        result = self.data[self.position:self.position + count]
        self.position += count
        return result

    def u8(self):
        return self.read(1)[0]

    def u16(self):
        return struct.unpack("<H", self.read(2))[0]

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]


def parse(raw, references, language_count=16):
    if language_count != len(LANGUAGES):
        raise ValueError("Unverified locale map")
    reader = Reader(raw)
    ditl, clng = reader.u32(), reader.u32()
    if ditl >= len(references) or clng >= len(references):
        raise ValueError("Invalid DITL/CLNG reference")
    leaves, containers, global_nodes = [], [], []
    by_type = {1: [], 2: [], 3: [], 4: []}
    while reader.position < len(raw) - 2:
        offset = reader.position
        kind = reader.u8()
        if kind == 1:
            header_start = reader.position
            variants = 1
            switch = None
            if reader.data[reader.position] == 1:
                flag = reader.u8()
                switch = {"reference_index": reader.u32(), "variant_count": reader.u32(),
                          "opaque_u32": reader.u32()}
                variants = switch["variant_count"]
                if switch["reference_index"] >= len(references) or not 1 <= variants <= 32:
                    raise ValueError("Invalid DLGE audio switch metadata")
                switch["resource_hash"] = references[switch["reference_index"]]["hash"]
                header = {"soundtag_hash": reader.u32(), "wav_name_hash": reader.u32(),
                          "opaque_u32": reader.u32(), "opaque_u8": flag}
            else:
                header = {"soundtag_hash": reader.u32(), "wav_name_hash": reader.u32(),
                          "opaque_u32": reader.u32(), "opaque_u8": reader.u8()}
            leaf = {"leaf_index": len(leaves), "offset": offset, **header, "languages": {},
                    "switch_metadata": switch, "raw_header_hex": raw[header_start:reader.position].hex()}
            for language in LANGUAGES:
                record_offset = reader.position
                wav_index = reader.u32()
                wav_indices = [wav_index]
                if wav_index != 0xFFFFFFFF:
                    wav_indices.extend(reader.u32() for _ in range(variants - 1))
                face_flag = reader.u8()
                face_index = reader.u32()
                length_offset = reader.position
                length = reader.u32()
                if length % 8:
                    raise ValueError(f"Unaligned DLGE caption at {reader.position - 4}")
                ciphertext = reader.read(length)
                for dependency in (*wav_indices, face_index):
                    if dependency != 0xFFFFFFFF and dependency >= len(references):
                        raise ValueError("Invalid localized audio/FaceFX reference")
                locale = {"audio_ref_index": wav_index, "face_ref_index": face_index,
                          "audio_ref_indices": wav_indices, "record_offset": record_offset,
                          "audio_hashes": [references[index]["hash"] for index in wav_indices if index != 0xFFFFFFFF],
                          "face_flag_raw": face_flag, "encrypted_text_length": length,
                          "length_offset": length_offset, "ciphertext_offset": length_offset + 4,
                          "audio_hash": references[wav_index]["hash"] if wav_index != 0xFFFFFFFF else None,
                          "face_hash": references[face_index]["hash"] if face_index != 0xFFFFFFFF else None}
                if language in ("source", "en", "pl"):
                    locale["text_raw"] = decrypt(ciphertext) if ciphertext else ""
                    locale.update(text_fields(locale["text_raw"]))
                leaf["languages"][language] = locale
            leaves.append(leaf)
            by_type[1].append(leaf)
        elif kind in (2, 3, 4):
            container = {"type": kind, "offset": offset, "switch_group": reader.u32(),
                         "default_switch": reader.u32(), "children": []}
            count = reader.u32()
            if count > (len(raw) - reader.position) // 6:
                raise ValueError("Invalid DLGE container child count")
            for _ in range(count):
                typed_index = reader.u16()
                metadata_count = reader.u32()
                if metadata_count > (len(raw) - reader.position) // 4:
                    raise ValueError("Invalid DLGE container metadata count")
                values = [reader.u32() for _ in range(metadata_count)]
                if typed_index >> 12 not in by_type:
                    raise ValueError("Unknown DLGE child type")
                container["children"].append({"typed_index": typed_index, "metadata_raw": values})
            containers.append(container)
            by_type[kind].append(container)
            # All container kinds share the footer's ordinal space. Type 4
            # also advances it, including when the root is a later type 2.
            global_nodes.append((kind, len(by_type[kind]) - 1))
        else:
            raise ValueError(f"Unknown DLGE section 0x{kind:02X} at {offset}")
    if reader.position != len(raw) - 2:
        raise ValueError("DLGE sections do not end at root pointer")
    root = reader.u16()
    root_type, root_index = root >> 12, root & 0xFFF
    if root_type in (2, 3, 4):
        if root_index >= len(global_nodes) or global_nodes[root_index][0] != root_type:
            raise ValueError("Invalid global root container pointer")
    elif root_type not in by_type or root_index >= len(by_type[root_type]):
        raise ValueError("Invalid root container pointer")
    return {"ditl_ref_index": ditl, "clng_ref_index": clng, "root_typed_index": root,
            "leaves": leaves, "containers": containers, "parsed_bytes": reader.position}


def replace_polish(raw, references, leaf_index, expected_raw_text, replacement_raw_text):
    """Change only one PL cipher block, keeping other locale records byte-identical."""
    before = parse(raw, references)
    locale = before["leaves"][leaf_index]["languages"]["pl"]
    if locale["text_raw"] != expected_raw_text:
        raise ValueError("Original Polish subtitle differs from mapped source")
    cipher, _ = upstream_crypto().xtea_encrypt(replacement_raw_text.encode("utf-8"))
    if decrypt(cipher) != replacement_raw_text:
        raise ValueError("New Polish subtitle crypto roundtrip failed")
    start, old_length = locale["length_offset"], locale["encrypted_text_length"]
    result = raw[:start] + struct.pack("<I", len(cipher)) + cipher + raw[start + 4 + old_length:]
    after = parse(result, references)
    if len(before["leaves"]) != len(after["leaves"]) or before["root_typed_index"] != after["root_typed_index"]:
        raise ValueError("Subtitle edit changed DLGE structure")
    for index, (original_leaf, final_leaf) in enumerate(zip(before["leaves"], after["leaves"])):
        for language in LANGUAGES:
            old, new = original_leaf["languages"][language], final_leaf["languages"][language]
            if (index, language) == (leaf_index, "pl"):
                if new["text_raw"] != replacement_raw_text:
                    raise ValueError("Edited PL text failed readback")
                continue
            old_start, new_start = old["record_offset"], new["record_offset"]
            old_end = old["ciphertext_offset"] + old["encrypted_text_length"]
            new_end = new["ciphertext_offset"] + new["encrypted_text_length"]
            if raw[old_start:old_end] != result[new_start:new_end]:
                raise ValueError("Subtitle edit modified another language")
    return result
