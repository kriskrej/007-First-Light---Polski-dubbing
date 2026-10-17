"""Check the selected Git snapshot before committing or publishing source code."""
import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_PARTS = {"Runtime", "Retail", "research", "artifacts", "backups", "cache", ".venv", "__pycache__"}
BLOCKED_SUFFIXES = {".wav", ".wem", ".rpkg", ".bnk", ".exe", ".dll", ".zip", ".7z", ".sqlite", ".sqlite3", ".db", ".pem", ".key", ".log"}
PATTERNS = {
    "private Windows path": re.compile(rb"[A-Za-z]:[\\/](?:Users|SteamLibrary)[\\/]", re.I),
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "assigned API credential": re.compile(rb"(?:ELEVENLABS_API_KEY(?:_2)?|xi-api-key)\s*[:=]\s*[\"']?([A-Za-z0-9_-]{24,})"),
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Inspect exact staged blobs rather than working files")
    parser.add_argument("--history", action="store_true", help="Check all reachable author/committer email addresses")
    args = parser.parse_args()
    names = git("ls-files", "-z").decode().strip("\0").split("\0")
    errors = []
    for name in filter(None, names):
        p = Path(name)
        if set(p.parts) & BLOCKED_PARTS or p.suffix.lower() in BLOCKED_SUFFIXES:
            errors.append(f"Forbidden publication path: {name}")
        if p.name.startswith(".env") and p.name != ".env.example":
            errors.append(f"Local environment file: {name}")
        data = git("show", ":" + name) if args.staged else (ROOT / p).read_bytes()
        if len(data) > 2 * 1024 * 1024:
            errors.append(f"Oversize source file: {name}")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"Non-text file: {name}")
        for label, pattern in PATTERNS.items():
            if pattern.search(data):
                errors.append(f"{label}: {name}")
    if args.history:
        for address in git("log", "--all", "--format=%ae%n%ce").decode().splitlines():
            if not address.endswith("@users.noreply.github.com"):
                errors.append("A reachable commit exposes a non-noreply email address")
    if errors:
        for error in sorted(set(errors)):
            print(error)
        raise SystemExit(1)
    print(f"Publication snapshot checked: {len(list(filter(None, names)))} UTF-8 source/documentation files")


if __name__ == "__main__":
    main()
