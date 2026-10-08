"""Resolve executables from the offline, platform-specific runtime payload."""

import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def host_key():
    arch = platform.machine().lower()
    arch = (
        "arm64"
        if arch in ("arm64", "aarch64")
        else "x64"
        if arch in ("amd64", "x86_64")
        else arch
    )
    host = {
        "win32": "windows",
        "darwin": "macos",
        "linux": "linux",
    }.get(sys.platform, sys.platform)

    return f"{host}-{arch}"


def configure():
    folder = (
        Path(os.environ["NAMAKI_RUNTIME_DIR"])
        if os.environ.get("NAMAKI_RUNTIME_DIR")
        else ROOT / "runtime" / host_key()
    )

    manifest = folder / "runtime.json"

    if not manifest.is_file():
        return None

    settings = json.loads(manifest.read_text(encoding="utf-8"))

    for key, variable in (
        ("qemu", "NAMAKI_QEMU"),
        ("browser", "NAMAKI_BROWSER"),
    ):
        executable = folder / settings[key]

        if not executable.is_file():
            raise RuntimeError(f"Incomplete offline runtime: {executable}")

        os.environ[variable] = str(executable)

    if settings.get("bios"):
        os.environ["NAMAKI_QEMU_BIOS"] = str(folder / settings["bios"])


    return settings