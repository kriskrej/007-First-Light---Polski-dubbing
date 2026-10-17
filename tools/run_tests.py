"""Run offline tests with a reproducible FFmpeg dependency and no API credentials."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    environment = os.environ.copy()
    for name in ("ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY_2"):
        environment.pop(name, None)
    if not shutil.which("ffmpeg"):
        try:
            import imageio_ffmpeg
        except ImportError:
            raise SystemExit("Install requirements-dev.txt or make FFmpeg available on PATH before testing.")
        source = Path(imageio_ffmpeg.get_ffmpeg_exe())
        tools = ROOT / "artifacts" / "test-tools"
        tools.mkdir(parents=True, exist_ok=True)
        executable = tools / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not executable.exists() or executable.stat().st_size != source.stat().st_size:
            shutil.copyfile(source, executable)
            executable.chmod(executable.stat().st_mode | 0o111)
        environment["PATH"] = str(tools) + os.pathsep + environment.get("PATH", "")
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=ROOT, env=environment, check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
