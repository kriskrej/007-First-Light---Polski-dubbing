"""Download and verify public dependencies into ignored local storage."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import urllib.request
import zipfile

from .common import ROOT, REPOSITORIES, cache_root, save_json, sha256

PACKAGES = (
    {"name": "italian-dub-v0.4", "tag": "v0.4",
     "url": "https://github.com/teogsxr/007-first-light-italian-dub/releases/download/v0.4/007-first-light-italian-dub-v0.4.zip",
     "size": 247213815, "sha256": "4b852a62362dd780e905f2d12b21fe395da71da56f5c6a8e5850243e59d99805"},
    {"name": "vgmstream-win64", "tag": "r2117",
     "url": "https://github.com/vgmstream/vgmstream/releases/download/r2117/vgmstream-win64.zip",
     "size": 4468180, "sha256": "6c4a8a3813864fefed081bbd337dbc0ad93bf88e0b92f5db98d7ab258b22dc6c"},
)

EXTRA_REPOSITORIES = {"bond-hashes": ("glacier-modding/Bond-Hashes", "5999e0d463757f6666578f7175e5d1a22021abca")}
REFERENCES = (
    ("AnthonyFuller/TonyTools", "de1462f8ea3890ccb2d3d8b22f1eaf8c4c025ac3", "Libraries/HMLanguages/src/Languages.cpp", "Languages.cpp", "bf1082ef1444975b528a17c4381b96eff2a0f3bd4c298e357a008ce32536e964"),
    ("glacier-modding/RPKG-Tool", "a7a3367640ab3304cf75b87fd9010025a161f137", "src/extract_dlge_to_json_from.cpp", "extract_dlge_to_json_from.cpp", "d68de30f6ee92c58c16042651acff7f0c9e3a3ba70a537c20d508bd68874a3e7"),
)


def ensure_sources():
    for name, (repository, commit) in {**REPOSITORIES, **EXTRA_REPOSITORIES}.items():
        directory = ROOT / "research" / name
        if not directory.exists():
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", "https://github.com/" + repository + ".git", str(directory)], check=True)
            if name == "italian-dub":
                subprocess.run(["git", "-C", str(directory), "sparse-checkout", "init", "--cone"], check=True)
                subprocess.run(["git", "-C", str(directory), "sparse-checkout", "set", "tools", "docs", "mod_manifest"], check=True)
            subprocess.run(["git", "-C", str(directory), "checkout", "--detach", commit], check=True)
        actual = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
        if actual != commit:
            raise ValueError(f"Unexpected source commit in {directory}; inspect before updating lock")
    folder = ROOT / "artifacts" / "reference"
    folder.mkdir(parents=True, exist_ok=True)
    for repository, commit, source_path, filename, expected_hash in REFERENCES:
        destination = folder / filename
        if not destination.exists():
            url = f"https://raw.githubusercontent.com/{repository}/{commit}/{source_path}"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
            if sha256(data) != expected_hash:
                raise ValueError("Reference source checksum mismatch")
            destination.write_bytes(data)
        if sha256(destination.read_bytes()) != expected_hash:
            raise ValueError("Local reference source checksum mismatch")


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(source) as archive:
        for entry in archive.infolist():
            normalized = entry.filename.replace("\\", "/")
            relative = PurePosixPath(normalized)
            if relative.is_absolute() or ".." in relative.parts or any(":" in part for part in relative.parts):
                raise ValueError("Unsafe ZIP member path")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("ZIP symlinks are not supported")
            target = (root / Path(*relative.parts)).resolve()
            if not target.is_relative_to(root):
                raise ValueError("ZIP member escapes destination")
            if entry.is_dir() or normalized.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as original, target.open("wb") as output:
                    shutil.copyfileobj(original, output)


def run():
    ensure_sources()
    downloads = cache_root() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    results = []
    for package in PACKAGES:
        archive = downloads / package["url"].rsplit("/", 1)[1]
        if not archive.exists():
            print(f"Downloading {package['name']}…", flush=True)
            temporary = archive.with_suffix(".part")
            with urllib.request.urlopen(package["url"], timeout=45) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if temporary.stat().st_size != package["size"] or file_hash(temporary) != package["sha256"]:
                raise ValueError("Downloaded package checksum or size mismatch")
            temporary.replace(archive)
        if archive.stat().st_size != package["size"] or file_hash(archive) != package["sha256"]:
            raise ValueError("Cached package checksum or size mismatch")
        destination = ROOT / "research" / package["name"]
        marker = destination / ".verified-sha256"
        if not marker.exists() or marker.read_text().strip() != package["sha256"]:
            safe_extract(archive, destination)
            marker.write_text(package["sha256"] + "\n")
        results.append({**package, "verified": True, "archive_path": str(archive), "extracted_path": str(destination)})
    repositories = []
    for name, (repository, commit) in REPOSITORIES.items():
        directory = ROOT / "research" / name
        actual = subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
        if actual != commit:
            raise ValueError(f"Unexpected source commit: {name}")
        repositories.append({"url": "https://github.com/" + repository, "commit": actual,
                             "remote_head_checked_date": "2026-10-14", "matches_research_reference": True})
    repositories.append({"url": "https://github.com/glacier-modding/Bond-Hashes",
                         "commit": subprocess.check_output(["git", "-C", str(ROOT / "research" / "bond-hashes"),
                                                            "rev-parse", "HEAD"], text=True).strip()})
    reference_files = [
        ("https://github.com/AnthonyFuller/TonyTools", "de1462f8ea3890ccb2d3d8b22f1eaf8c4c025ac3", "Languages.cpp"),
        ("https://github.com/glacier-modding/RPKG-Tool", "a7a3367640ab3304cf75b87fd9010025a161f137", "extract_dlge_to_json_from.cpp"),
    ]
    report = {"checked_date": "2026-10-14", "repositories": repositories, "downloads": results,
              "additional_reference_files": [{"repository_url": url, "commit": commit, "file": filename,
                                              "sha256": sha256((ROOT / "artifacts" / "reference" / filename).read_bytes())}
                                             for url, commit, filename in reference_files],
              "rpkg_tool_pr_103": {"url": "https://github.com/glacier-modding/RPKG-Tool/pull/103",
                                   "state": "open", "merged": False, "head_commit": reference_files[1][1]},
              "engine_validation": False}
    provenance = ROOT / "docs" / "SOURCES.json"
    existing = json.loads(provenance.read_text(encoding="utf-8")) if provenance.exists() else {}
    existing.update(report)
    report = existing
    save_json(provenance, report)
    return report
