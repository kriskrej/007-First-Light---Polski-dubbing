"""Align subtitle segments using returned TTS character timings."""
from array import array
import json
import math
import subprocess

from .audio import ffmpeg, read_wav, write_wav, bounds
from .common import ROOT, sha256


def load_take(generated, alignment_path, rate):
    resampled = generated.parent / "resampled.wav"
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(generated),
                    "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le", str(resampled)], check=True, capture_output=True)
    _, source = read_wav(resampled)
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))["alignment"]
    if not alignment:
        raise ValueError("Missing alignment for multi-segment speech")
    text = "".join(alignment["characters"])
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    if len(text) != len(starts) or len(text) != len(ends):
        raise ValueError("Invalid character alignment shape")
    return source, text, starts, ends


def prepare_segments(row, generated, *, cached_takes=None):
    rate = row["sample_rate"]
    source, text, starts, ends = load_take(generated, ROOT / row["alignment_path"], rate)
    _, original = read_wav(ROOT / row["original_wav_path"])
    alternatives = []
    for entry in cached_takes or []:
        path = ROOT / entry["audio_path"]
        if sha256(path.read_bytes()) != entry["audio_sha256"]:
            raise ValueError("Cached alternative take checksum changed")
        alternatives.append((*load_take(path, ROOT / entry["alignment_path"], rate), entry["request_id"]))
    segments = row.get("spoken_segments") or row.get("display_segments") or [segment["text"] for segment in row["segments_pl"]]
    if len(segments) != len(row["segments_pl"]):
        raise ValueError("Spoken and displayed segment counts differ")
    targets = [round(float(segment["timing_raw"].split(",")[0]) * rate) for segment in row["segments_pl"]]
    if targets != sorted(set(targets)) or targets[0] < 0 or targets[-1] >= row["sample_count"]:
        raise ValueError("Subtitle start times do not fit audio")
    ranges, cursor = [], 0
    preserved = set(row.get("preserve_original_segment_indices", []))
    if not preserved.issubset(range(len(segments))):
        raise ValueError("Invalid original segment selection")
    for segment_index, segment in enumerate(segments):
        if segment_index in preserved:
            ranges.append(None)
            continue
        index = text.casefold().find(segment.casefold(), cursor)
        if index < 0:
            raise ValueError("Spoken segment does not match returned alignment")
        last = index + len(segment) - 1
        while last > index and not text[last].isalnum():
            last -= 1
        first = index
        while first < last and not text[first].isalnum():
            first += 1
        begin = max(0, round(starts[first] * rate) - round(rate * 0.02))
        end = min(len(source), round(ends[last] * rate) + round(rate * 0.04))
        if end <= begin:
            raise ValueError("Empty aligned speech segment")
        chosen_source, chosen_request = source, row.get("request_id")
        for samples, candidate_text, candidate_starts, candidate_ends, request_id in alternatives:
            if candidate_text.casefold().count(segment.casefold()) != 1:
                continue
            candidate_index = candidate_text.casefold().find(segment.casefold())
            first_char, last_char = candidate_index + first - index, candidate_index + last - index
            a = max(0, round(candidate_starts[first_char] * rate) - round(rate * .02))
            b = min(len(samples), round(candidate_ends[last_char] * rate) + round(rate * .04))
            if b <= a:
                continue
            active_a, active_b = bounds(samples[a:b], rate)
            current_a, current_b = bounds(chosen_source[begin:end], rate)
            if active_b - active_a < current_b - current_a:
                chosen_source, begin, end, chosen_request = samples, a, b, request_id
        ranges.append((chosen_source, begin, end, chosen_request))
        cursor = index + len(segment)
    final = array("h", [0]) * row["sample_count"]
    qa = []
    for index, source_range in enumerate(ranges):
        target_start = targets[index]
        target_end = targets[index + 1] if index + 1 < len(targets) else len(final)
        subtitle_end = round(float(row["segments_pl"][index]["timing_raw"].split(",")[1]) * rate)
        if subtitle_end <= target_start:
            raise ValueError("Invalid subtitle end time")
        target_end = min(target_end, subtitle_end)
        if index in preserved:
            final[target_start:target_end] = original[target_start:target_end]
            qa.append({"segment": index, "target_start": target_start / rate,
                       "original_segment_preserved": True, "tempo": 1})
            continue
        segment_source, begin, end, source_request = source_range
        # Keep a small tail within each window rather than cutting all silence.
        tail = min(round(rate * 0.03), (target_end - target_start) // 20)
        capacity = target_end - target_start - tail
        speech = segment_source[begin:end]
        active_start, active_end = bounds(speech, rate)
        speech = speech[active_start:active_end]
        factor = len(speech) / capacity
        item = {"segment": index, "target_start": target_start / rate, "capacity": capacity / rate,
                "source_active_duration": len(speech) / rate, "required_tempo": factor,
                "source_request_id": source_request}
        qa.append(item)
        if factor > 1.15:
            row["segment_timing_qa"] = qa
            raise ValueError(f"Segment {index} needs adaptation (tempo {factor:.3f})")
        if factor > 1:
            before = generated.parent / f"segment-{index}-before.wav"
            after = generated.parent / f"segment-{index}-after.wav"
            write_wav(before, rate, speech)
            subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(before),
                            "-af", f"atempo={factor:.9f}", "-c:a", "pcm_s16le", str(after)], check=True, capture_output=True)
            _, speech = read_wav(after)
        if len(speech) > target_end - target_start:
            raise ValueError("Aligned segment would overlap next subtitle")
        final[target_start:target_start + len(speech)] = speech
        item["tempo"] = max(1, factor)
        item["final_speech_end"] = (target_start + len(speech)) / rate
    rms = math.sqrt(sum(value * value for value in final) / len(final))
    peak = max(map(abs, final))
    if not rms:
        raise ValueError("Segment fitting produced silence")
    target_rms = 32768 * 10 ** (row["original_rms_dbfs"] / 20)
    gain = min(target_rms / rms, 32768 * 10 ** (-1 / 20) / peak)
    final = array("h", (round(value * gain) for value in final))
    for index in preserved:
        end = targets[index + 1] if index + 1 < len(targets) else len(final)
        final[targets[index]:end] = original[targets[index]:end]
    output = generated.parent / "prepared.wav"
    write_wav(output, rate, final)
    row["segment_timing_qa"] = qa
    row["timing_qa"] = {"status": "subtitle_segments_matched", "sample_count": len(final),
                        "generated_total_duration": len(source) / rate,
                        "tempo": max(item["tempo"] for item in qa), "gain_db": 20 * math.log10(gain),
                        "target_starts_preserved": True}
    return output


def prepare_campaign_segments(row, generated, *, cached_takes=None):
    """Use at most 200 ms of an existing inter-caption pause, without new TTS."""
    try:
        return prepare_segments(row, generated, cached_takes=cached_takes)
    except ValueError as error:
        qa = row.get("segment_timing_qa") or []
        markers = row.get("original_markers", {})
        cues = markers.get("cue ", {}).get("points", [])
        if not ("needs adaptation (tempo" in str(error) and qa and row.get("chapter_id")
                and cues and all(cue["sample_offset"] == 0 for cue in cues) and "smpl" not in markers
                and row.get("audio_variant_count", 1) == 1
                and not row.get("preserve_original_segment_indices")
                and row.get("reviewed_subtitle_segments") is None):
            raise
        item = qa[-1]
        index = item["segment"]
        if index == 0 or len(qa) < 2:
            raise
        shift = math.ceil((item["source_active_duration"] / 1.15 - item["capacity"] + .02) * 100) / 100
        if not 0 < shift <= .2:
            raise
        segments = [dict(segment) for segment in row["segments_pl"]]
        start_raw, end_raw = segments[index]["timing_raw"].split(",")
        start, end = map(float, (start_raw, end_raw))
        new_start = round(start - shift, 5)
        if qa[-2].get("final_speech_end", start) + .03 > new_start:
            raise
        previous_start_raw, previous_end_raw = segments[index - 1]["timing_raw"].split(",")
        previous_start, previous_end = map(float, (previous_start_raw, previous_end_raw))
        if new_start <= previous_start:
            raise
        if previous_end > new_start:
            segments[index - 1]["timing_raw"] = previous_start_raw + "," + str(new_start)
        segments[index]["timing_raw"] = str(new_start) + "," + end_raw
        candidate = dict(row)
        candidate.update(original_segments_pl=[dict(segment) for segment in row["segments_pl"]],
                         segments_pl=segments, reviewed_subtitle_segments=segments,
                         subtitle_timing_override_reason=f"Bounded campaign policy: move caption {index} {shift:.2f}s earlier into verified quiet, retain full words, original audio frames, other locales and <=1.15 tempo.")
        try:
            prepared = prepare_segments(candidate, generated, cached_takes=cached_takes)
        except ValueError:
            raise error
        candidate["pause_fit_review"] = {"segment": index, "earlier_seconds": shift, "new_paid_requests": 0,
                                         "original_frame_count_retained": True}
        row.update(candidate)
        return prepared
