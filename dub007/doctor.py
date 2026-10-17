import os
from pathlib import Path
import platform
import re
import shutil
import sys
import zlib

from .common import ARTIFACTS, ROOT, api_key, cache_root, save_json, sha256, upstream


def find_wwise():
    result = []
    explicit = os.environ.get("WWISE_CONSOLE") or shutil.which("WwiseConsole")
    if explicit and Path(explicit).is_file():
        result.append(str(Path(explicit).resolve()))
    roots = [Path(os.environ.get("SystemDrive", "C:") + "\\") / "Audiokinetic"]
    roots.extend(Path(os.environ[variable]) / "Audiokinetic"
                 for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
                 if os.environ.get(variable))
    for directory in roots:
        if directory.is_dir():
            result.extend(str(path.resolve()) for path in directory.rglob("WwiseConsole.exe"))
    return sorted(set(result))


def steam_info():
    if os.name != "nt":
        raise RuntimeError("Steam detection currently supports Windows only")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
        steam_path = Path(winreg.QueryValueEx(key, "SteamPath")[0])
    library_file = steam_path / "steamapps" / "libraryfolders.vdf"
    libraries = [steam_path]
    if library_file.is_file():
        libraries.extend(Path(value.replace("\\\\", "\\")) for value in
                         re.findall(r'"path"\s+"([^"]+)"', library_file.read_text(encoding="utf-8")))
    for library in dict.fromkeys(libraries):
        manifest = library / "steamapps" / "appmanifest_3768760.acf"
        if not manifest.is_file():
            continue
        text = manifest.read_text(encoding="utf-8")
        values = dict(re.findall(r'"([^"]+)"\s+"([^"]*)"', text))
        if values.get("appid") != "3768760":
            continue
        game = library / "steamapps" / "common" / values["installdir"]
        if game.resolve() == ROOT.resolve():
            return {"appid": values["appid"], "buildid": values["buildid"],
                    "game_path": str(game), "steam_path": str(steam_path),
                    "libraries": [str(item) for item in dict.fromkeys(libraries)],
                    "appmanifest_sha256": sha256(manifest.read_bytes())}
    raise RuntimeError("Current folder does not match a verified Steam appmanifest")


def run():
    codec = upstream("pkgdef")
    definition = (ROOT / "Runtime" / "packagedefinition.txt").read_bytes()
    header, crc, plain = codec.decrypt(definition)
    crc_valid = crc == zlib.crc32(plain)
    roundtrip = codec.encrypt(header, plain) == definition
    if not crc_valid or not roundtrip:
        raise RuntimeError("packagedefinition verification failed")
    path = ARTIFACTS / "source" / "packagedefinition.plain.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plain)
    report = {
        "environment": {"os": platform.platform(), "python": sys.version.split()[0],
                        "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
                        "wwise_console": find_wwise(), "cache_path": str(cache_root()),
                        "cache_free_bytes": shutil.disk_usage(cache_root()).free},
        "steam": steam_info(),
        "packagedefinition": {"sha256": sha256(definition), "header_hex": header.hex(),
                              "crc_valid": crc_valid, "byte_identical_roundtrip": roundtrip,
                              "partitions": re.findall(rb"@partition[^\r\n]+", plain)},
        "elevenlabs": {"key_present": bool(api_key())},
    }
    report["packagedefinition"]["partitions"] = [line.decode("utf-8") for line in report["packagedefinition"]["partitions"]]
    from .tts import usage
    report["elevenlabs"]["tts_usage"] = usage()
    save_json(ARTIFACTS / "doctor.json", report)
    return report
