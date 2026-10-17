"""Explicitly reviewed spatial dialogue; keep the mono fitting path strict."""
from array import array
import json
import math
import wave

from .audio import bounds, read_wav, write_wav
from .common import ROOT


def read_pcm(path):
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() not in (2, 6):
            raise ValueError("Reviewed multichannel path requires 16-bit stereo or six-channel PCM")
        return stream.getframerate(), array("h", stream.readframes(stream.getnframes()))


def original_profile(row, wav):
    wav = wav.resolve()
    policy = json.loads((ROOT / "config/surround.json").read_text(encoding="utf-8"))
    review = policy["resources"].get(row["audio_hash_en"])
    if not review or review["speaker_id"] != row["speaker_id"]:
        raise ValueError("Multichannel resource needs explicit source and encoder review before TTS")
    count = review.get("channels", 6)
    mask = "0x3" if count == 2 else "0x60f"
    if count not in (2, 6) or row["channels"] != count or f"channel mask: {mask} /" not in row["vgmstream_metadata_raw"]:
        raise ValueError("Unreviewed surround channel layout")
    rate, samples = read_pcm(wav)
    if rate != row["sample_rate"] or len(samples) != row["sample_count"] * count:
        raise ValueError("Original surround decoder profile mismatch")
    channels = [samples[index::count] for index in range(count)]
    levels = [math.sqrt(sum(value * value for value in channel) / len(channel)) for channel in channels]
    reference = levels.index(max(levels))
    if not levels[reference] or (count == 6 and levels[3] != 0):
        raise ValueError("Reviewed PA layout requires audible speech and a silent LFE")
    onsets = [bounds(channel, rate)[0] if level else 0 for channel, level in zip(channels, levels)]
    first = min(onset for onset, level in zip(onsets, levels) if level)
    delays = ([onset - first if level else 0 for onset, level in zip(onsets, levels)]
              if review.get("project_measured_onsets") else [0] * count)
    if max(delays) > round(rate * .15):
        raise ValueError("PA channel onset spread exceeds reviewed 150 ms")
    reference_path = wav.with_name(wav.stem + "-reference.wav")
    write_wav(reference_path, rate, channels[reference])
    row.update(original_wav_path=str(wav.relative_to(ROOT)),
               original_peak_dbfs=20 * math.log10(max(map(abs, samples)) / 32768),
               original_rms_dbfs=20 * math.log10(math.sqrt(sum(v * v for v in samples) / len(samples)) / 32768),
               surround_profile={"channel_mask": mask, "channel_order": ["FL", "FR"] if count == 2 else ["FL", "FR", "FC", "LFE", "SL", "SR"],
                   "reference_channel": reference, "reference_path": str(reference_path.relative_to(ROOT)),
                   "gains": [level / levels[reference] for level in levels], "delays_samples": delays,
                   "reference_peak_dbfs": 20 * math.log10(max(map(abs, channels[reference])) / 32768),
                   "reference_rms_dbfs": 20 * math.log10(levels[reference] / 32768),
                   "encoder_roundtrip": review["encoder_roundtrip"],
                   "scope": "Measured channel levels and onset spread; original reverberation is not reconstructed."})


def project_channels(mono, gains, delays):
    count = len(gains)
    if count not in (2, 6) or len(delays) != count or (count == 6 and gains[3] != 0):
        raise ValueError("Invalid reviewed PA projection")
    if any(not 0 <= gain <= 1 for gain in gains) or any(not isinstance(delay, int) or not 0 <= delay < len(mono) for delay in delays):
        raise ValueError("Invalid surround gain or delay")
    if any(delay and max(map(abs, mono[-delay:])) > 70 for delay in delays):
        raise ValueError("Surround delay would discard audible speech tail")
    output = array("h", [0]) * (len(mono) * count)
    for index, (gain, delay) in enumerate(zip(gains, delays)):
        if gain:
            output[index + delay * count::count] = array("h", (round(value * gain) for value in mono[:len(mono) - delay]))
    return output


def prepare(row, generated, cached_takes):
    from .audio import prepare_campaign
    from .timing import prepare_campaign_segments
    profile = row["surround_profile"]
    saved = {key: row[key] for key in ("channels", "original_wav_path", "original_peak_dbfs", "original_rms_dbfs")}
    proxy = dict(row)
    proxy.update(channels=1, original_wav_path=profile["reference_path"],
                 original_peak_dbfs=profile["reference_peak_dbfs"], original_rms_dbfs=profile["reference_rms_dbfs"])
    try:
        if len(row["segments_pl"]) > 1:
            prepared = prepare_campaign_segments(proxy, generated, cached_takes=cached_takes)
        else:
            prepared = prepare_campaign(proxy, generated)
    finally:
        row.update(proxy)
        row.update(saved)
    rate, mono = read_wav(prepared)
    projected = project_channels(mono, profile["gains"], profile["delays_samples"])
    prefix_frames = round(float(row["segments_pl"][0]["timing_raw"].split(",")[0]) * rate)
    if prefix_frames:
        _, original = read_pcm(ROOT / row["original_wav_path"])
        projected[:prefix_frames * row["channels"]] = original[:prefix_frames * row["channels"]]
    output = generated.parent / "prepared-surround.wav"
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(row["channels"])
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(projected.tobytes())
    row["surround_qa"] = {"sample_frames": len(mono), "channels": row["channels"], "lfe_silent": row["channels"] == 6,
                          "original_prefix_frames_retained": prefix_frames,
                          "audible_tail_discarded": False, "original_reverb_reconstructed": False}
    return output
