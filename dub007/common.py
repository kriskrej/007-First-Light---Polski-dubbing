import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
REPOSITORIES = {
    "italian-dub": ("teogsxr/007-first-light-italian-dub", "04896264e87b37e17637bb2e2ae0348a66639c9a"),
    "toolbox": ("derx05/gaming-007FirstLight-toolbox", "bba21551493da9e1877c93782be437e46a29c323"),
    "toolkit": ("7akeem0/007-firstlight-toolkit", "ee8d6790b9f9f459687496229658235bffd13f4e"),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def upstream(name):
    repository = "toolkit" if name.startswith("glacier") else "toolbox"
    directory = ROOT / "research" / repository
    if not (directory / ".git").exists():
        raise RuntimeError(f"Missing source repository: {directory}")
    commit = subprocess.check_output(
        ["git", "-C", str(directory), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != REPOSITORIES[repository][1]:
        raise RuntimeError(f"Unexpected upstream commit in {directory}")
    source = directory if repository == "toolkit" else directory / "tools"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    return importlib.import_module(name)


def local_setting(name):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = ROOT / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            field, separator, value = line.strip().partition("=")
            if separator and field == name:
                return value.strip().strip('"').strip("'")
    return ""


def active_account():
    account = local_setting("DUB007_ELEVENLABS_ACCOUNT") or "1"
    if account not in ("1", "2"):
        raise ValueError("DUB007_ELEVENLABS_ACCOUNT must be 1 or 2")
    return account


def api_key(account=None):
    account = account or active_account()
    if account not in ("1", "2"):
        raise ValueError("Unknown ElevenLabs account")
    return local_setting("ELEVENLABS_API_KEY" if account == "1" else "ELEVENLABS_API_KEY_2")


def cache_root():
    override = os.environ.get("DUB007_CACHE")
    local = os.environ.get("LOCALAPPDATA")
    directory = Path(override) if override else (Path(local) / "dub007" if local else ARTIFACTS / "cache")
    identifier = sha256(str(ROOT).casefold().encode())[:16]
    result = directory / identifier
    result.mkdir(parents=True, exist_ok=True)
    return result
