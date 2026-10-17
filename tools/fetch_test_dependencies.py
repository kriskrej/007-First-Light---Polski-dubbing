"""Fetch only the pinned source dependencies required by offline tests."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    "toolbox": ("https://github.com/derx05/gaming-007FirstLight-toolbox.git", "bba21551493da9e1877c93782be437e46a29c323"),
    "toolkit": ("https://github.com/7akeem0/007-firstlight-toolkit.git", "ee8d6790b9f9f459687496229658235bffd13f4e"),
}


def main():
    for name, (url, commit) in SOURCES.items():
        folder = ROOT / "research" / name
        if not folder.exists():
            folder.mkdir(parents=True)
            subprocess.run(["git", "init", str(folder)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(folder), "remote", "add", "origin", url], check=True)
        if not (folder / ".git").exists():
            raise RuntimeError(f"Refusing an unrecognised dependency directory: {name}")
        existing = subprocess.run(["git", "-C", str(folder), "rev-parse", "HEAD"], capture_output=True, text=True)
        if existing.returncode == 0:
            if existing.stdout.strip() != commit:
                raise RuntimeError(f"Dependency {name} has a different commit; inspect it before changing anything")
        else:
            subprocess.run(["git", "-C", str(folder), "fetch", "--depth=1", url, commit], check=True)
            subprocess.run(["git", "-C", str(folder), "checkout", "--detach", commit], check=True)
        actual = subprocess.check_output(["git", "-C", str(folder), "rev-parse", "HEAD"], text=True).strip()
        if actual != commit:
            raise RuntimeError(f"Dependency verification failed: {name}")
        print(f"Verified {name}: {commit}")


if __name__ == "__main__":
    main()
