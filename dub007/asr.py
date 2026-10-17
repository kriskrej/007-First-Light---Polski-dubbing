"""Optional free local ASR evidence, independent of paid ElevenLabs generation."""
import json
import urllib.request
import shutil
import subprocess

from .audio import ffmpeg, read_wav
from .common import ARTIFACTS, ROOT, save_json
from .dependencies import file_hash

REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
MODEL_SHA256 = "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"
MODEL_SIZE = 147951465


def model(language="pl"):
    filename = "ggml-small.en.bin" if language == "en" else "ggml-base.bin"
    expected_size = 487614201 if language == "en" else MODEL_SIZE
    expected_sha = "c6138d6d58ecc8322097e0f987c32f1be8bb0a18532a3f88f734d1bbf9c41e5d" if language == "en" else MODEL_SHA256
    path = ARTIFACTS / "asr" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{REVISION}/{filename}"
    if not path.exists():
        temporary = path.with_suffix(".download")
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if temporary.stat().st_size != expected_size or file_hash(temporary) != expected_sha:
            raise ValueError("Whisper model size/checksum mismatch")
        temporary.rename(path)
    if path.stat().st_size != expected_size or file_hash(path) != expected_sha:
        raise ValueError("Local Whisper model checksum mismatch")
    return path


def transcribe(wav, language, output):
    path = model(language)
    output.parent.mkdir(parents=True, exist_ok=True)
    model_name = path.relative_to(ROOT).as_posix()
    output_name = output.relative_to(ROOT).as_posix()
    _, samples = read_wav(wav)
    peak = max(map(abs, samples))
    if not peak:
        raise ValueError("Cannot transcribe silence")
    gain = 30000 / peak
    filter_text = f"volume={gain:.6f},aresample=16000,whisper=model='{model_name}':language={language}:use_gpu=false:queue=30:destination='{output_name}':format=text"
    result = subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "info", "-i", str(wav),
                             "-af", filter_text, "-f", "null", "-"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    output.with_suffix(".log").write_text(result.stderr, encoding="utf-8")
    if result.returncode or not output.exists():
        raise RuntimeError("Local ASR failed; see ignored ASR log")
    return output.read_text(encoding="utf-8").strip()


def originals():
    manifest = json.loads((ARTIFACTS / "poc" / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for row in manifest["rows"]:
        output = ARTIFACTS / "poc" / "asr" / f"{row['audio_hash_en']}-en-small.txt"
        text = output.read_text(encoding="utf-8").strip() if output.exists() else transcribe(ROOT / row["original_wav_path"], "en", output)
        results.append({"audio_hash": row["audio_hash_en"], "scene_line_number": row["scene_line_number"],
                        "expected_en": row["text_en"], "transcribed": text})
        save_json(ARTIFACTS / "poc" / "asr-originals.json", results)
        print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    return results
