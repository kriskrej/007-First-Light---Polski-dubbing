"""Wwise external-source conversion with verified local CLI syntax."""
import json
import re
import struct
import subprocess
import uuid
import wave
from pathlib import Path
import xml.etree.ElementTree as ET

from .common import ARTIFACTS, ROOT, save_json, sha256
from .doctor import find_wwise
from .selection import metadata, riff_metadata


def console():
    paths = find_wwise()
    if not paths:
        raise RuntimeError("WwiseConsole is missing; install Wwise Authoring")
    return Path(paths[-1])


def invoke(arguments):
    result = subprocess.run([str(console()), *map(str, arguments)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode not in (0, 2):
        raise RuntimeError(f"Wwise failed ({result.returncode}): {result.stdout[-2000:]} {result.stderr[-500:]}")
    return result.stdout + result.stderr


def project():
    directory = ARTIFACTS / "wwise" / "Dub007"
    path = directory / "Dub007.wproj"
    if not path.exists():
        invoke(["create-new-project", path, "--platform", "Windows"])
    version = ET.parse(path).getroot().attrib
    workunit = directory / "Conversion Settings" / "Dub007.wwu"
    if not workunit.exists():
        factory = console().parents[3] / "Data" / "Default Project" / "Conversion Settings" / "Factory Conversion Settings.wwu"
        root = ET.parse(factory).getroot()
        conversion = next(item for item in root.iter("Conversion") if item.get("Name") == "Vorbis Quality High")
        conversion.set("Name", "Dub007 Vorbis Preserve")
        conversion.set("ID", "{" + str(uuid.uuid4()).upper() + "}")
        identifier = "{" + str(uuid.uuid4()).upper() + "}"
        document = ET.Element("WwiseDocument", Type="WorkUnit", ID=identifier, SchemaVersion=version["SchemaVersion"])
        unit = ET.SubElement(ET.SubElement(document, "Conversions"), "WorkUnit", Name="Dub007", ID=identifier, PersistMode="Standalone")
        ET.SubElement(unit, "ChildrenList").append(conversion)
        ET.indent(document)
        ET.ElementTree(document).write(workunit, encoding="utf-8", xml_declaration=True)
    return path


def convert(wav, output):
    wav, output = Path(wav).resolve(), Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element("ExternalSourcesList", SchemaVersion="1", Root=str(wav.parent))
    ET.SubElement(root, "Source", Path=wav.name, Destination=output.stem,
                  Conversion="Dub007 Vorbis Preserve")
    sources = output.with_suffix(".wsources")
    ET.ElementTree(root).write(sources, encoding="utf-8", xml_declaration=True)
    log = invoke(["convert-external-source", project(), "--platform", "Windows",
                  "--source-file", sources, "--output", "Windows", output.parent])
    output.with_suffix(".wwise.log").write_text(log, encoding="utf-8")
    if not output.is_file():
        raise RuntimeError("Wwise did not write the requested WEM")
    return output


def restore_markers(encoded, original, sample_count):
    data = Path(encoded).read_bytes()
    profile = riff_metadata(data)
    chunks = []
    for chunk in profile["riff_chunks"]:
        if chunk["id"] in ("cue ", "LIST", "smpl"):
            continue
        start = chunk["offset"] + 8
        chunks.append((chunk["id"].encode("ascii"), data[start:start + chunk["size"]]))
    marker_chunks = []
    for name, marker in original.items():
        raw = bytes.fromhex(marker["raw_hex"])
        if name == "cue ":
            if any(point["sample_offset"] > sample_count for point in marker["points"]):
                raise ValueError("Original cue lies beyond final WEM duration")
        elif name == "smpl":
            count = struct.unpack_from("<I", raw, 28)[0]
            for index in range(count):
                start, end = struct.unpack_from("<II", raw, 36 + index * 24 + 8)
                if start > end or end >= sample_count:
                    raise ValueError("Original loop lies outside final WEM")
        elif name != "LIST":
            raise ValueError("Unsupported marker chunk")
        marker_chunks.append((name.encode("ascii"), raw))
    # Runtime parsers can finish scanning the header at the data chunk.
    # Every locally inspected First Light original places cue/LIST before data;
    # keeping their bytes but appending them after audio loses that invariant.
    data_positions = [index for index, (name, _) in enumerate(chunks) if name == b"data"]
    if len(data_positions) != 1:
        raise ValueError("Marker restoration requires exactly one audio data chunk")
    position = data_positions[0]
    chunks[position:position] = marker_chunks
    # Observed Wwise RIFF chunks in the originals and converter are unpadded.
    body = b"WAVE" + b"".join(identifier + struct.pack("<I", len(payload)) + payload
                              for identifier, payload in chunks)
    final = b"RIFF" + struct.pack("<I", len(body)) + body
    riff_metadata(final)
    Path(encoded).write_bytes(final)


def roundtrip():
    manifest = json.loads((ARTIFACTS / "poc" / "manifest.json").read_text(encoding="utf-8"))
    row = manifest["rows"][0]
    output = ARTIFACTS / "wwise" / "roundtrip" / "original-en.wem"
    convert(ROOT / row["original_wav_path"], output)
    vgm = ROOT / "research" / "vgmstream-win64" / "vgmstream-cli.exe"
    before = metadata(vgm, output)
    if (before["sample_rate"], before["channels"], before["sample_count"]) != (
            row["sample_rate"], row["channels"], row["sample_count"]):
        raise ValueError("Encoder changed channels, sample rate or sample count")
    restore_markers(output, row["original_markers"], before["sample_count"])
    measured = metadata(vgm, output)
    decoded = output.with_suffix(".wav")
    subprocess.run([str(vgm), "-i", "-o", str(decoded), str(output)], capture_output=True, check=True)
    with wave.open(str(decoded), "rb") as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getnframes()) != (
                row["sample_rate"], row["channels"], row["sample_count"]):
            raise ValueError("Final decoder samples disagree with original")
    report = {"status": "offline_roundtrip_passed", "console_path": str(console()),
              "console_sha256": sha256(console().read_bytes()),
              "console_help": invoke(["convert-external-source", "--help"]),
              "original_audio_hash": row["audio_hash_en"], "metadata": measured,
              "final_riff": riff_metadata(output.read_bytes()), "wem_sha256": sha256(output.read_bytes()),
              "runtime_verified": False}
    save_json(ARTIFACTS / "wwise" / "roundtrip.json", report)
    return report
