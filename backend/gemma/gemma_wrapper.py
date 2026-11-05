"""GEMMA cross-platform wrapper for Windows/WSL environments.

Translates Windows paths into WSL paths and delegates execution to the
official GEMMA binary located at backend/gemma/gemma.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


def win_to_wsl_path(path_str: str) -> str:
    """Convert Windows path (e.g. D:\\foo\\bar or D:/foo/bar) to WSL path (/mnt/d/foo/bar)."""
    if not path_str or path_str.startswith("-"):
        return path_str

    norm = re.sub(r"[/\\]+", "/", path_str)
    match = re.match(r"^([a-zA-Z]):/(.*)$", norm)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).lstrip("/")
        return f"/mnt/{drive}/{rest}"

    try:
        p = Path(path_str)
        if p.exists() or (
            ("/" in norm)
            and p.parent != Path(".")
            and p.parent.exists()
        ):
            resolved = p.resolve()
            norm_res = re.sub(r"[/\\]+", "/", str(resolved))
            match_res = re.match(r"^([a-zA-Z]):/(.*)$", norm_res)
            if match_res:
                return f"/mnt/{match_res.group(1).lower()}/{match_res.group(2).lstrip('/')}"
    except Exception:
        pass
    return path_str


def main():
    script_dir = Path(__file__).resolve().parent
    gemma_linux_binary = script_dir / "gemma"

    # Convert binary path to WSL
    wsl_gemma = win_to_wsl_path(str(gemma_linux_binary))

    translated_args = []
    for arg in sys.argv[1:]:
        if ":" in arg or "\\" in arg or "/" in arg:
            translated_args.append(win_to_wsl_path(arg))
        else:
            translated_args.append(arg)

    cmd = ["wsl", "--exec", wsl_gemma] + translated_args
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
