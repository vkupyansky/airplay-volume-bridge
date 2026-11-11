"""Манифест проверяемой сборки без локальных путей и данных пользователя."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--header", type=Path)
    options = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    project = tomllib.loads((root / "pyproject.toml").read_text())
    version: str = project["project"]["version"]
    if options.header is not None:
        options.header.write_text('#define BRIDGE_VERSION "' + version + '"\n')
        return
    packages = ("libconfig", "popt", "libssl", "libavutil", "libavcodec")
    dependencies = {
        name: subprocess.check_output(
            ["pkg-config", "--modversion", name], text=True
        ).strip()
        for name in packages
    }
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((root / "patches").glob("*.patch"))
    }
    manifest = {
        "version": version,
        "receiver": "Shairport Sync 5.6.3",
        "architecture": platform.machine(),
        "macos": platform.mac_ver()[0],
        "dependencies": dependencies,
        "patches_sha256": hashes,
    }
    (root / "work/bin/build-info.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
