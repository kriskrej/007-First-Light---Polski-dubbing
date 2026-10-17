"""Audit installed facial references and repair WEM header ordering offline."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import uuid
import wave

from .archive import open_index, parse_tables, iter_entries, read_resource, refs
from .common import ARTIFACTS, ROOT, save_json, sha256
from .encoding import restore_markers
from .selection import riff_metadata


def chunk_payloads(raw):
    return [(c["id"], raw[c["offset"] + 8:c["offset"] + 8 + c["size"]])
            for c in riff_metadata(raw)["riff_chunks"]]


def audit():
    from .deployment import active_install
    from .dlge import parse
    from .installer import fingerprints

    installed = active_install()
    if not installed or fingerprints(ROOT) != installed["base_archive_fingerprints"]:
        raise ValueError("Installed base fingerprint mismatch")
    index = open_index()
    streams, findings, face_hashes = {}, [], set()
    report = {"checked_utc": datetime.now(timezone.utc).isoformat(), "install_id": installed["install_id"],
              "wwes_checked": 0, "wwes_markers_after_data": 0, "dlge_checked": 0,
              "english_facefx_or_dialogue_changes": [], "missing_face_dependencies": [],
              "runtime_verified": False}
    previous_report = ROOT / "docs/LIPSYNC_AUDIT.json"
    if previous_report.exists():
        reproduction = json.loads(previous_report.read_text(encoding="utf-8")).get("user_reproduction")
        if reproduction:
            report["user_reproduction"] = reproduction
    try:
        for patch in installed["patches"]:
            path = ROOT / "Runtime" / patch["filename"]
            if sha256(path.read_bytes()) != patch["sha256"]:
                raise ValueError("Installed patch checksum changed")
            with path.open("rb") as stream:
                for h, kind, _, offset, flags, size, _, _, raw_refs, _, _ in iter_entries(path, parse_tables(path)):
                    if kind not in ("DLGE", "WWES"):
                        continue
                    new = read_resource(stream, {"offset": offset, "flags": flags, "size": size})
                    if kind == "WWES":
                        profile = riff_metadata(new)
                        order = [c["id"] for c in profile["riff_chunks"]]
                        report["wwes_checked"] += 1
                        if any(order.index(marker) > order.index("data") for marker in ("cue ", "LIST", "smpl") if marker in order):
                            report["wwes_markers_after_data"] += 1
                        continue
                    original = index.execute("SELECT * FROM resources WHERE hash=? AND type='DLGE'", (h,)).fetchall()
                    if len(original) != 1:
                        raise ValueError("Ambiguous original facial dialogue")
                    entry = original[0]
                    if entry["refs"] != raw_refs:
                        raise ValueError("Installed DLGE dependency references differ from original")
                    archive = entry["archive"]
                    if archive not in streams:
                        streams[archive] = (ROOT / "Runtime" / archive).open("rb")
                    old = read_resource(streams[archive], entry)
                    before, after = parse(old, refs(entry)), parse(new, refs(entry))
                    report["dlge_checked"] += 1
                    if len(before["leaves"]) != len(after["leaves"]) or before["containers"] and [
                            {k: v for k, v in c.items() if k != "offset"} for c in before["containers"]] != [
                            {k: v for k, v in c.items() if k != "offset"} for c in after["containers"]]:
                        findings.append({"hash": h, "reason": "Dialogue structure changed"})
                    for a, b in zip(before["leaves"], after["leaves"]):
                        x, y = a["languages"]["en"], b["languages"]["en"]
                        if x.get("face_hash"):
                            face_hashes.add(x["face_hash"])
                        def record(raw, locale):
                            return raw[locale["record_offset"]:locale["ciphertext_offset"] + locale["encrypted_text_length"]]
                        if a["raw_header_hex"] != b["raw_header_hex"] or record(old, x) != record(new, y):
                            findings.append({"hash": h, "leaf": a["leaf_index"], "reason": "EN header/audio/FaceFX record changed"})
        report["english_facefx_or_dialogue_changes"] = findings
        report["english_face_dependencies_checked"] = len(face_hashes)
        report["missing_face_dependencies"] = [h for h in sorted(face_hashes) if not index.execute(
            "SELECT 1 FROM resources WHERE hash=?", (h,)).fetchone()]
    finally:
        for stream in streams.values():
            stream.close()
        index.close()
    report["conclusion"] = (
        "Marker order differs from native files; causal link to reported cutscene issue requires gameplay A/B validation"
        if report["wwes_markers_after_data"] else
        "Native marker header order restored; cutscene lipsync still awaits gameplay validation")
    save_json(ROOT / "docs/LIPSYNC_AUDIT.json", report)
    return report


def repair():
    from .campaign import database, save_row
    from .installer import durable_bytes

    store = database()
    pointer = ARTIFACTS / "campaign/marker-repair-current.json"
    report = json.loads(pointer.read_text(encoding="utf-8")) if pointer.exists() else {
        "repair_id": uuid.uuid4().hex, "status": "preparing", "started_utc": datetime.now(timezone.utc).isoformat(),
        "repairs": {}, "new_paid_requests": 0, "runtime_verified": False}
    folder = ARTIFACTS / "campaign" / "marker-repairs" / report["repair_id"]
    folder.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(folder).free < 2_000_000_000:
        raise RuntimeError("Insufficient room for staged WEM and decoder validation")
    save_json(pointer, report)
    try:
        rows = [json.loads(d) for d, in store.database.execute(
            "SELECT document FROM resources WHERE status='offline_final_wem_validated' ORDER BY position")]
        for row in rows:
            h = row["audio_hash_en"]
            source = ROOT / row["wem_path"]
            raw = source.read_bytes()
            if sha256(raw) != row["wem_sha256"]:
                raise ValueError("WEM checksum changed before marker repair")
            order = [c["id"] for c in riff_metadata(raw)["riff_chunks"]]
            if all(order.index(marker) < order.index("data") for marker in row["original_markers"]):
                if row.get("marker_order_repair"):
                    report["repairs"][h] = row["marker_order_repair"]
                continue
            output = folder / f"{h}.wem"
            durable_bytes(output, raw)
            restore_markers(output, row["original_markers"], row["final_audio_qa"]["metadata"]["sample_count"])
            new = output.read_bytes()
            # Every codec, hash and compressed audio byte must be unchanged.
            keep = lambda items: [(name, value) for name, value in items if name not in ("cue ", "LIST", "smpl")]
            if keep(chunk_payloads(raw)) != keep(chunk_payloads(new)):
                raise ValueError("Marker repair changed codec or compressed audio")
            profile = riff_metadata(new)
            if profile["original_markers"] != row["original_markers"]:
                raise ValueError("Marker repair changed marker bytes")
            decoded = folder / f"{h}.wav"
            subprocess.run([str(ROOT / "research/vgmstream-win64/vgmstream-cli.exe"), "-o", str(decoded), str(output)],
                           capture_output=True, check=True)
            with wave.open(str(decoded), "rb") as wav:
                expected = row["final_audio_qa"]["metadata"]
                if (wav.getframerate(), wav.getnchannels(), wav.getnframes(), wav.getsampwidth()) != (
                        expected["sample_rate"], expected["channels"], expected["sample_count"], 2):
                    raise ValueError("Reordered WEM decoder profile changed")
                pcm = wav.readframes(wav.getnframes())
            old_wav = source.with_suffix(".wav")
            if not old_wav.exists():
                subprocess.run([str(ROOT / "research/vgmstream-win64/vgmstream-cli.exe"), "-o", str(old_wav), str(source)],
                               capture_output=True, check=True)
            with wave.open(str(old_wav), "rb") as wav:
                if wav.readframes(wav.getnframes()) != pcm:
                    raise ValueError("Reordered WEM changed decoded PCM")
            change = {"repair_id": report["repair_id"], "original_wem_path": row["wem_path"],
                      "original_wem_sha256": row["wem_sha256"], "repaired_wem_sha256": sha256(new),
                      "compressed_audio_unchanged": True, "decoded_pcm_unchanged": True,
                      "marker_bytes_unchanged": True, "markers_before_data": True}
            row.update(wem_path=str(output.relative_to(ROOT)), wem_sha256=sha256(new),
                       marker_order_repair=change, install_status="awaiting_install")
            save_row(store, row)
            report["repairs"][h] = change
            if len(report["repairs"]) % 100 == 0:
                save_json(pointer, report)
                print(json.dumps({"marker_headers_repaired": len(report["repairs"]), "total": len(rows)}), flush=True)
        report.update(status="offline_validated_awaiting_install", repaired_audio=len(report["repairs"]),
                      completed_utc=datetime.now(timezone.utc).isoformat())
        save_json(pointer, report)
        summary = {k: v for k, v in report.items() if k != "repairs"}
        summary["journal"] = str(pointer.relative_to(ROOT))
        save_json(ROOT / "docs/LIPSYNC_REPAIR.json", summary)
        return summary
    finally:
        store.close()
