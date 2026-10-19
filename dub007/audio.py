"""Conservative sample-count and level matching for the PoC dialogue."""
from array import array
import math
import os
from pathlib import Path
import shutil
import subprocess
import wave

from .common import ROOT, sha256
from .encoding import convert, restore_markers
from .selection import metadata, riff_metadata


def ffmpeg():
    path = shutil.which("ffmpeg")
    if path:
        return path
    directory = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Packages"
    paths = list(directory.glob("Gyan.FFmpeg*/**/ffmpeg.exe"))
    if not paths:
        raise RuntimeError("ffmpeg missing")
    return str(paths[0])


def read_wav(path):
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() != 1:
            raise ValueError("PoC processing supports only verified 16-bit mono WAV")
        return stream.getframerate(), array("h", stream.readframes(stream.getnframes()))


def bounds(samples, rate):
    block = max(1, rate // 100)
    threshold = max(32768 * 10 ** (-48 / 20), max(map(abs, samples)) * 10 ** (-35 / 20))
    active = [start for start in range(0, len(samples), block)
              if math.sqrt(sum(value * value for value in samples[start:start + block]) /
                           len(samples[start:start + block])) > threshold]
    if not active:
        raise ValueError("Audio is silent")
    return max(0, active[0] - block * 2), min(len(samples), active[-1] + block * 3)


def write_wav(path, rate, samples):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())


def has_one_polish_segment(row):
    """Recognize reviewed new PL captions without overwriting empty original metadata."""
    original = row.get("segments_pl", [])
    if original:
        return len(original) == 1
    reviewed = row.get("reviewed_subtitle_segments") or []
    displayed = row.get("display_segments") or []
    return bool(row.get("translation_source") == "agent_en_fallback_missing_pl"
                and not row.get("text_pl_original") and row.get("source_audio_review")
                and row.get("subtitle_timing_override_reason")
                and len(reviewed) == len(displayed) == 1
                and displayed[0] and reviewed[0].get("text") == displayed[0]
                and row.get("text_pl_display") == displayed[0])


def target_samples(row):
    extension = row.get("reviewed_duration_extension_seconds", 0)
    if extension:
        cues = row.get("original_markers", {}).get("cue ", {}).get("points", [])
        if not (0 < extension <= 0.2 and row.get("chapter_id") and row.get("duration_extension_reason")
                and has_one_polish_segment(row) and cues
                and all(point["sample_offset"] == 0 for point in cues)
                and "smpl" not in row["original_markers"]):
            raise ValueError("Duration extension requires reviewed single-segment chapter speech without loops or internal cues")
    return row["sample_count"] + round(extension * row["sample_rate"])


def prepare(row, generated, *, resampled_audio=None):
    final_count = target_samples(row)
    folder = generated.parent
    resampled = resampled_audio or folder / "resampled.wav"
    if resampled_audio is None:
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(generated),
                        "-ar", str(row["sample_rate"]), "-ac", str(row["channels"]),
                        "-c:a", "pcm_s16le", str(resampled)], check=True, capture_output=True)
    rate, samples = read_wav(resampled)
    original_rate, original = read_wav(ROOT / row["original_wav_path"])
    if original_rate != rate:
        raise ValueError("Unexpected original sample rate")
    source_start, source_end = bounds(samples, rate)
    padding_reduction = row.get("edge_padding_reduction_seconds", 0)
    if not 0 <= padding_reduction <= 0.015:
        raise ValueError("Reviewed silent edge padding reduction must not exceed 15 ms")
    edge = round(padding_reduction * rate)
    trimmed_leading = trimmed_trailing = 0
    if edge:
        quiet_threshold = max(32768 * 10 ** (-48 / 20), max(map(abs, samples)) * 10 ** (-35 / 20))
        def quiet(values):
            return values and math.sqrt(sum(value * value for value in values) / len(values)) <= quiet_threshold
        # bounds adds 20 ms before and after active blocks. Keep at least 5 ms
        # of that guard and remove only measured quiet padding, never speech.
        if source_start > 0 and quiet(samples[source_start:source_start + edge]):
            source_start += edge
            trimmed_leading = edge
        if source_end < len(samples) and quiet(samples[source_end - edge:source_end]):
            source_end -= edge
            trimmed_trailing = edge
    original_start, original_end = bounds(original, rate)
    reference_onset = original_start
    onset_shift = row.get("available_onset_shift_seconds", 0)
    if not 0 <= onset_shift <= 0.1:
        raise ValueError("Reviewed onset adjustment must not exceed 100 ms")
    original_start = max(0, original_start - round(onset_shift * rate))
    preserved_prefix = round(row.get("preserve_original_prefix_until", 0) * rate)
    if preserved_prefix < 0 or preserved_prefix >= len(original):
        raise ValueError("Invalid original prefix range")
    original_start = max(original_start, preserved_prefix)
    source = samples[source_start:source_end]
    available = original_end - original_start
    factor = len(source) / available
    tail_consumed = 0
    minimum_tail = row.get("minimum_tail_seconds", 0.03)
    if not 0.01 <= minimum_tail <= 0.03:
        raise ValueError("Reviewed minimum tail must retain 10 to 30 ms")
    # Use only reviewed existing end silence, preserving total frames and a
    # final quiet guard. Keep the 15% tempo limit and all audible speech.
    if factor > 1.15:
        tail_allowance = row.get("available_tail_extension_seconds", 0.06)
        cues = row.get("original_markers", {}).get("cue ", {}).get("points", [])
        campaign_tail = (row.get("chapter_id") and has_one_polish_segment(row)
                         and cues and all(point["sample_offset"] == 0 for point in cues)
                         and "smpl" not in row.get("original_markers", {})
                         and row.get("reviewed_existing_tail_policy"))
        if not 0 <= tail_allowance <= (0.5 if campaign_tail else 0.25):
            raise ValueError("Tail allowance exceeds reviewed quiet policy")
        extra = min(round(rate * tail_allowance), max(0, final_count - original_end - round(rate * minimum_tail)))
        if extra and len(source) / (available + extra) <= 1.15:
            available += extra
            tail_consumed = extra
            factor = len(source) / available
    if factor > 1.15:
        if len(source) <= available * 1.15 + round(rate * 0.001):
            factor = 1.15
    if factor > 1.15:
        row["timing_qa"] = {"status": "needs_shorter_text_or_retake", "required_stretch": factor,
                            "generated_active_duration": len(source) / rate,
                            "original_active_duration": available / rate}
        raise ValueError(f"Line {row['scene_line_number']} needs shorter text or retake (tempo {factor:.3f})")
    if factor > 1:
        trimmed = folder / "active.wav"
        stretched = folder / "stretched.wav"
        write_wav(trimmed, rate, source)
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed),
                        "-af", f"atempo={factor:.9f}", "-c:a", "pcm_s16le", str(stretched)],
                       check=True, capture_output=True)
        _, source = read_wav(stretched)
        # atempo has a small output-duration error for very short speech.
        # Reprocess the same uncut input with a measured correction, retaining
        # the 15% speed limit. Never truncate an audible word to fit the file.
        capacity = min(available, final_count - original_start)
        for correction in range(3):
            if len(source) <= capacity:
                break
            corrected = factor * len(source) / capacity * 1.001
            if corrected > 1.15:
                break
            factor = corrected
            subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed),
                            "-af", f"atempo={factor:.9f}", "-c:a", "pcm_s16le", str(stretched)],
                           check=True, capture_output=True)
            _, source = read_wav(stretched)
        if len(source) > available + int(rate * 0.03):
            raise ValueError("Stretch duration exceeded allowed 30-ms rounding margin")
    # Preserve original onset and leave its tail, using padding instead of extending speech.
    if original_start + len(source) > final_count:
        raise ValueError("Speech would exceed final sample count")
    final = array("h", [0]) * original_start + source
    final.extend(array("h", [0]) * (final_count - len(final)))
    inserted = 0
    if row.get("sentence_sync"):
        sync = row["sentence_sync"]
        split = round(sync["split_after_seconds"] * rate)
        target = round(sync["target_start_seconds"] * rate)
        inserted = target - split
        if inserted < 0 or split <= original_start or split >= original_start + len(source):
            raise ValueError("Invalid verified sentence synchronization point")
        # Only consume existing tail silence; never truncate speech to create a pause.
        if inserted and (inserted > len(final) or max(map(abs, final[-inserted:])) > 70):
            raise ValueError("Sentence synchronization would cut audible tail")
        if inserted:
            final = final[:split] + array("h", [0]) * inserted + final[split:-inserted]
    rms = math.sqrt(sum(value * value for value in final) / len(final))
    peak = max(map(abs, final))
    target_rms = 32768 * 10 ** (row["original_rms_dbfs"] / 20)
    gain = min(target_rms / rms, (32768 * 10 ** (-1 / 20)) / peak)
    final = array("h", (round(value * gain) for value in final))
    if preserved_prefix:
        final[:preserved_prefix] = original[:preserved_prefix]
    prepared = folder / "prepared.wav"
    write_wav(prepared, rate, final)
    row["timing_qa"] = {"status": "offline_matched", "generated_total_duration": len(samples) / rate,
                        "source_active_duration": (source_end - source_start) / rate,
                        "original_active_duration": available / rate, "tempo": max(1, factor),
                        "original_onset": original_start / rate, "tail_seconds": (len(final) - original_start - len(source) - inserted) / rate,
                        "reference_onset": reference_onset / rate,
                        "onset_adjustment_seconds": (reference_onset - original_start) / rate,
                        "inserted_sentence_pause": inserted / rate,
                        "original_tail_used_seconds": tail_consumed / rate,
                        "trimmed_leading_padding_seconds": trimmed_leading / rate,
                        "trimmed_trailing_padding_seconds": trimmed_trailing / rate,
                        "reviewed_duration_extension_seconds": (final_count - row["sample_count"]) / rate,
                        "gain_db": 20 * math.log10(gain), "sample_count": len(final)}
    return prepared


def prepare_campaign(row, generated):
    """Retry timing failures using measured quiet only; never request more TTS."""
    try:
        return prepare(row, generated)
    except ValueError as error:
        message = str(error)
        timing_error = ("needs shorter text or retake" in message or message in (
            "Speech would exceed final sample count",
            "Stretch duration exceeded allowed 30-ms rounding margin"))
        markers = row.get("original_markers", {})
        cues = markers.get("cue ", {}).get("points", [])
        eligible = (timing_error and row.get("chapter_id") and has_one_polish_segment(row)
                    and cues and all(point["sample_offset"] == 0 for point in cues) and "smpl" not in markers)
        if not eligible or (row.get("available_onset_shift_seconds", 0) == .1
                            and row.get("minimum_tail_seconds", .03) == .01):
            raise
        candidate = dict(row)
        candidate.update(available_onset_shift_seconds=.1, minimum_tail_seconds=.01)
        try:
            prepared = prepare(candidate, generated)
        except ValueError:
            candidate.update(available_tail_extension_seconds=.5,
                             reviewed_existing_tail_policy="Use at most 500ms of measured existing tail; preserve original frames, <=1.15 tempo and >=10ms final quiet.")
            try:
                prepared = prepare(candidate, generated)
            except ValueError:
                raise error
        candidate["quiet_fit_review"] = {
            "policy": "Campaign-only, one zero cue, no loop; use existing measured quiet, <=100 ms earlier onset, <=500ms of existing tail, >=10 ms final quiet, <=1.15 tempo.",
            "new_paid_requests": 0, "original_frame_count_retained": target_samples(row) == row["sample_count"],
            "new_duration_extension": False}
        row.update(candidate)
        return prepared


def verify_original_spans(row, prepared):
    """Prove preserved foreign/nonverbal PCM survives Polish fitting exactly."""
    prefix_seconds = row.get('preserve_original_prefix_until', 0)
    preserved = set(row.get('preserve_original_segment_indices', []))
    if not prefix_seconds and not preserved:
        return None
    rate, original = read_wav(ROOT / row['original_wav_path'])
    final_rate, final = read_wav(prepared)
    if rate != final_rate or rate != row['sample_rate']:
        raise ValueError('Preserved original audio sample rate changed')
    ranges = []
    if prefix_seconds:
        count = round(prefix_seconds * rate)
        if not 0 < count < len(original):
            raise ValueError('Invalid preserved original prefix')
        ranges.append((0, count))
    if preserved:
        segments = row.get('segments_pl', [])
        targets = [round(float(segment['timing_raw'].split(',')[0]) * rate) for segment in segments]
        if targets != sorted(set(targets)) or any(not 0 <= start < len(original) for start in targets):
            raise ValueError('Invalid preserved original segment boundaries')
        for index in sorted(preserved):
            if not isinstance(index, int) or not 0 <= index < len(targets):
                raise ValueError('Invalid preserved original segment index')
            ranges.append((targets[index], targets[index + 1] if index + 1 < len(targets) else len(original)))
    for start, end in ranges:
        if end > len(final) or final[start:end] != original[start:end]:
            raise ValueError('Original-language PCM changed during Polish fitting')
    report = {'status':'original_pcm_identical_before_encoding', 'sample_rate':rate,
              'preserved_sample_ranges':[list(pair) for pair in ranges],
              'prepared_wav_sha256':sha256(prepared.read_bytes())}
    row['preserved_original_audio_qa'] = report
    return report


def encode(row, prepared, output_directory=None):
    verify_original_spans(row, prepared)
    final_count = target_samples(row)
    output = (output_directory or ROOT / "artifacts" / "poc" / "final") / f"{row['audio_hash_en']}.wem"
    convert(prepared, output)
    return validate_encoded(row, output)


def validate_encoded(row, output):
    """Restore this resource's markers and decode its actual output profile."""
    output = Path(output)
    final_count = target_samples(row)
    vgm = ROOT / "research" / "vgmstream-win64" / "vgmstream-cli.exe"
    before = metadata(vgm, output)
    if (before["sample_rate"], before["channels"], before["sample_count"]) != (
            row["sample_rate"], row["channels"], final_count):
        raise ValueError("Final conversion changed audio profile")
    restore_markers(output, row["original_markers"], before["sample_count"])
    after = metadata(vgm, output)
    decoded = output.with_suffix(".wav")
    subprocess.run([str(vgm), "-i", "-o", str(decoded), str(output)], check=True, capture_output=True)
    if row["channels"] == 1:
        rate, final = read_wav(decoded)
    else:
        from .surround import read_pcm
        rate, final = read_pcm(decoded)
        expected_mask = "0x3" if row["channels"] == 2 else "0x60f"
        if row["channels"] not in (2, 6) or f"channel mask: {expected_mask} /" not in after["vgmstream_metadata_raw"]:
            raise ValueError("Final WEM changed reviewed surround layout")
        if row["channels"] == 6 and any(final[3::6]):
            raise ValueError("Final PA WEM introduced LFE content")
    if len(final) != final_count * row["channels"] or rate != row["sample_rate"] or max(map(abs, final)) == 0:
        raise ValueError("Final WEM failed decoder sample validation")
    markers = riff_metadata(output.read_bytes())["original_markers"]
    if markers != row["original_markers"]:
        raise ValueError("Final WEM markers differ from original")
    row["final_audio_qa"] = {"metadata": after, "peak_dbfs": 20 * math.log10(max(map(abs, final)) / 32768),
                            "rms_dbfs": 20 * math.log10(math.sqrt(sum(value * value for value in final) / len(final)) / 32768),
                            "markers_identical": True, "runtime_verified": False}
    return output
