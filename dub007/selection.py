"""Prepare opening-scene candidates and original WEM/WAV QA without TTS."""
from contextlib import ExitStack
from array import array
import json
import math
import re
import struct
import subprocess
import wave

from .archive import open_index, read_resource
from .common import ARTIFACTS, ROOT, save_json, sha256

SCENE = "s1005_cloverintrocinematic"


def native_labels(markers):
    raw = bytes.fromhex(markers.get("LIST", {}).get("raw_hex", ""))
    if not raw.startswith(b"adtl"):
        return []
    labels, position = [], 4
    while position + 8 <= len(raw):
        identifier, size = struct.unpack_from("<4sI", raw, position)
        end = position + 8 + size
        if end > len(raw):
            raise ValueError("Truncated native LIST label")
        if identifier == b"labl" and size >= 4:
            cue_id = struct.unpack_from("<I", raw, position + 8)[0]
            text = raw[position + 12:end].split(b"\0")[0].decode("utf-8")
            labels.append({"cue_id": cue_id, "label": text})
        position = end
        if size % 2 and position < len(raw) and raw[position] == 0:
            position += 1
    return labels


def riff_metadata(data):
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("WWES is not a raw RIFF/WAVE payload")
    declared = struct.unpack_from("<I", data, 4)[0] + 8
    if declared != len(data):
        raise ValueError("RIFF declared size differs from payload size")
    chunks, markers = [], {}
    position = 12
    profile = {}
    while position < len(data):
        if position + 8 > len(data):
            raise ValueError("Truncated RIFF chunk")
        identifier, length = struct.unpack_from("<4sI", data, position)
        end = position + 8 + length
        if end > len(data):
            raise ValueError("RIFF chunk outside payload")
        payload = data[position + 8:end]
        name = identifier.decode("ascii")
        chunks.append({"id": name, "offset": position, "size": length})
        if identifier == b"fmt ":
            codec, channels, rate = struct.unpack_from("<HHI", payload)
            profile = {"format_tag": codec, "channels": channels, "sample_rate": rate,
                       "original_fmt_hex": payload.hex()}
        elif identifier in (b"cue ", b"LIST", b"smpl"):
            markers[name] = {"raw_hex": payload.hex()}
            if identifier == b"cue ":
                count = struct.unpack_from("<I", payload)[0]
                if 4 + count * 24 != len(payload):
                    raise ValueError("Unexpected cue table shape")
                markers[name]["points"] = [dict(zip(("id", "position", "data_chunk", "chunk_start", "block_start", "sample_offset"),
                                                   struct.unpack_from("<II4sIII", payload, 4 + index * 24))) for index in range(count)]
                for point in markers[name]["points"]:
                    point["data_chunk"] = point["data_chunk"].decode("ascii")
        # Wwise may omit final alignment padding; preserve observed chunks.
        position = end
        if length % 2 and position < len(data) and data[position] == 0:
            position += 1
    if not profile:
        raise ValueError("Missing fmt chunk")
    return {**profile, "riff_chunks": chunks, "original_markers": markers}


def metadata(executable, path):
    result = subprocess.run([str(executable), "-m", str(path)], capture_output=True, text=True, check=True)
    rate = re.search(r"sample rate:\s*(\d+)", result.stdout)
    samples = re.search(r"stream total samples:\s*(\d+)", result.stdout)
    channels = re.search(r"channels:\s*(\d+)", result.stdout)
    encoding = re.search(r"encoding:\s*(.+)", result.stdout)
    if not all((rate, samples, channels, encoding)):
        raise ValueError("Unrecognized vgmstream metadata output")
    return {"sample_rate": int(rate[1]), "sample_count": int(samples[1]), "channels": int(channels[1]),
            "duration": int(samples[1]) / int(rate[1]), "original_codec": encoding[1].strip(),
            "vgmstream_metadata_raw": result.stdout}


def run():
    existing_manifest = ARTIFACTS / "poc" / "manifest.json"
    if existing_manifest.exists():
        existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
        if any(row.get("generation_status") != "not_started" for row in existing.get("rows", [])):
            raise RuntimeError("PoC generation already started; do not overwrite its manifest with prepare-poc")
    casting = json.loads((ROOT / "config" / "casting.json").read_text(encoding="utf-8"))
    database = open_index()
    hash_list = json.loads((ROOT / "research" / "bond-hashes" / "paths" / "WWES.json").read_text(encoding="utf-8"))
    paths = {item["hash"]: item["path"] for item in hash_list
             if f"/{SCENE}_" in item["path"] and "/english(us)/" in item["path"]}
    matching = {}
    for line in (ARTIFACTS / "texts" / "dialogue-mapped.jsonl").open(encoding="utf-8"):
        record = json.loads(line)
        audio_hash = record.get("audio_hash_en")
        if audio_hash in paths:
            if audio_hash in matching and (record["text_en"], record["text_pl_original"], record["speaker_id"]) != (
                    matching[audio_hash]["text_en"], matching[audio_hash]["text_pl_original"], matching[audio_hash]["speaker_id"]):
                raise ValueError("Conflicting use of opening audio")
            matching[audio_hash] = record
    vgmstream = ROOT / "research" / "vgmstream-win64" / "vgmstream-cli.exe"
    if not vgmstream.exists():
        raise RuntimeError("Run sources to download verified vgmstream")
    folder = ARTIFACTS / "poc" / "originals"
    folder.mkdir(parents=True, exist_ok=True)
    candidates = []
    with ExitStack() as stack:
        streams = {}
        for audio_hash, record in matching.items():
            entries = database.execute("SELECT * FROM resources WHERE hash=? AND type='WWES'", (audio_hash,)).fetchall()
            if len(entries) != 1:
                raise ValueError("Opening audio has ambiguous archive placement")
            entry = entries[0]
            archive = entry["archive"]
            if archive not in streams:
                streams[archive] = stack.enter_context((ROOT / "Runtime" / archive).open("rb"))
            payload = read_resource(streams[archive], entry)
            wem = folder / f"{audio_hash}.wem"
            wav = folder / f"{audio_hash}.wav"
            wem_unchanged = wem.exists() and sha256(wem.read_bytes()) == sha256(payload)
            wem.write_bytes(payload)
            profile = riff_metadata(payload)
            measured = metadata(vgmstream, wem)
            if (profile["sample_rate"], profile["channels"]) != (measured["sample_rate"], measured["channels"]):
                raise ValueError("RIFF and decoder metadata disagree")
            if not wav.exists() or not wem_unchanged:
                subprocess.run([str(vgmstream), "-i", "-o", str(wav), str(wem)], check=True, capture_output=True)
            with wave.open(str(wav), "rb") as stream:
                if (stream.getframerate(), stream.getnchannels(), stream.getnframes(), stream.getsampwidth()) != (
                        measured["sample_rate"], measured["channels"], measured["sample_count"], 2):
                    raise ValueError("Decoded WAV metadata differs from vgmstream samples")
                samples = array("h", stream.readframes(stream.getnframes()))
            peak = max(abs(sample) for sample in samples)
            rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
            if not peak:
                raise ValueError("Original selected WEM decoded to silence")
            measured["original_peak_dbfs"] = 20 * math.log10(peak / 32768)
            measured["original_rms_dbfs"] = 20 * math.log10(rms / 32768)
            ordinal = re.search(r"_(\d{3})(?:_alt\d+)?\.wav", paths[audio_hash])
            tts = record["text_pl_original"].replace("3 minuty", "trzy minuty")
            if tts.isupper():
                tts = tts.capitalize()
            row = {**record, **profile, **measured, "scene_id": SCENE,
                   "scene_line_number": int(ordinal[1]) if ordinal else 999,
                   "text_pl_display": record["text_pl_original"], "text_pl_tts": tts,
                   "audio_source_path": paths[audio_hash], "active_archive": archive,
                   "chunk": archive.split(".")[0], "original_payload_sha256": sha256(payload),
                   "original_wem_path": str(wem.relative_to(ROOT)), "original_wav_path": str(wav.relative_to(ROOT)),
                   "raw_resource_refs_hex": entry["refs"].hex(), "sysmem": entry["sysmem"], "vidmem": entry["vidmem"],
                   "generation_status": "not_started", "install_status": "not_installed",
                   "qa_status": "reference_and_original_decode_verified",
                   "voice_id": casting["speakers"].get(record["speaker_id"], {}).get("voice_id"),
                   "model_id": casting["model_id"], "settings": casting["settings"],
                   "generated_audio_path": None, "wem_path": None, "wem_sha256": None,
                   "segment_ids": [segment["segment_id"] for segment in record["segments_en"]]}
            candidates.append(row)
    candidates.sort(key=lambda row: row["scene_line_number"])
    eligible = [row for row in candidates if row["duration"] <= 6 and row["text_pl_original"]]
    selected = eligible[:10]
    if len(selected) != 10:
        raise ValueError("Fewer than 10 short, mapped opening lines")
    save_json(ARTIFACTS / "poc" / "candidates.json", candidates)
    save_json(ARTIFACTS / "poc" / "manifest.json", {"status": "awaiting_encoder_roundtrip", "scene_id": SCENE,
                                                   "tts_characters_sent": 0, "rows": selected})
    table = ["# Próbka: początkowa scena Clover", "", "Stan: `awaiting_encoder_roundtrip`. Nie wykonano jeszcze płatnego TTS, instalacji ani testu w grze.",
             "", "Scena źródłowa: `s1005_cloverintrocinematic`, rozmowa podczas lotu śmigłowcem otwierającego pierwszą misję. Wywołanie: start nowej gry lub powtórzenie pierwszej misji z audio EN i napisami PL. Nie nadpisywać istniejącego zapisu; wybór bezpiecznej opcji powtórzenia pozostaje do sprawdzenia w menu użytkownika.",
             "", "Mapowanie pochodzi z pól języka EN w DLGE; oficjalny tekst PL odczytano z bloku 9. Ścieżki z Bond-Hashes są pomocnicze. Oryginały zdekodowano vgmstream; odsłuch/ASR i właściwe pojawienie się w grze pozostają do potwierdzenia.",
             "", "| Nr sceny | Speaker | EN | PL | WWES hash | Długość EN | Codec | Markery | Gra |",
             "|---:|---|---|---|---|---:|---|---|---|"]
    for row in selected:
        table.append(f"| {row['scene_line_number']} | {row['speaker_id']} | {row['text_en'].replace('|', '/')} | {row['text_pl_original'].replace('|', '/')} | `{row['audio_hash_en']}` | {row['duration']:.3f} s | {row['original_codec']} / {row['sample_rate']} Hz | {', '.join(row['original_markers'])} | nie testowano |")
    (ROOT / "docs" / "POC.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    database.close()
    return {"status": "awaiting_encoder_roundtrip", "scene_id": SCENE, "selected_lines": len(selected),
            "tts_characters_before_tags": sum(len(row["text_pl_tts"]) for row in selected), "tts_characters_sent": 0,
            "original_audio_decoded": len(candidates), "manifest_path": str(ARTIFACTS / "poc" / "manifest.json")}
