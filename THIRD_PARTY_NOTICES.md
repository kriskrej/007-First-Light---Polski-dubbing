# Third-party notices

Portions of the archive reader, patch writer and packagedefinition handling are adapted from **derx05/gaming-007FirstLight-toolbox**. Reference text/decryption code is loaded from **7akeem0/007-firstlight-toolkit**. The Italian dubbing project supplied reference installer and manifest concepts. Original author attribution is retained independently of this project's authorship.

Copies of the available upstream notices are included in `licenses/`:

- `toolbox-LICENSE.txt` — MIT, Copyright (c) 2026 derx05.
- `toolkit-LICENSE.txt` — MIT, Copyright (c) 2026 Hesham (@7akeem0).
- `italian-dub-LICENSE.txt` and `italian-dub-NOTICE.md` — upstream scope and distribution notices.

These licenses concern the upstream code and documentation. They do not license the game, extracted data, voice models or generated audio. The optional standalone recipient installer bundles CPython, the lz4 Python extension and a PyInstaller bootloader. Their applicable notices accompany the local release candidate. It does not bundle Wwise, FFmpeg, ElevenLabs credentials or voice models.

Python packages are pinned in `requirements.txt`. Any packaged distribution must retain the licenses of dependencies it actually bundles.

The recipient release retains CPython's notices (including bundled runtime components), lz4's license, the PyInstaller bootloader exception, the OpenSSL 3.0.13 Apache license, the XZ/liblzma upstream licensing notice and the SQLite public-domain reference. No Wwise or FFmpeg binary is included. The frozen executable's contents were inspected before sealing the candidate archive.

`requirements-dev.txt` additionally pins **imageio-ffmpeg 0.6.0** (imageio contributors, BSD-2-Clause wrapper) for test environments. Its platform wheel supplies an FFmpeg executable with separate upstream licensing. These downloaded binaries stay outside Git and are not part of the mod release. Review the relevant binary build's notices before any future redistribution.
