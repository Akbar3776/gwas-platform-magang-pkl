"""bcftools cross-platform wrapper for Windows/WSL environments.

Translates Windows paths (D:\\... or D:/...) into WSL paths (/mnt/d/...)
and executes bcftools inside the WSL environment.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


def win_to_wsl_path(path_str: str) -> str:
    """Convert Windows path to WSL path."""
    norm = path_str.replace("\\", "/")
    match = re.match(r"^([a-zA-Z]):/(.*)$", norm)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2)
        return f"/mnt/{drive}/{rest}"
    try:
        p = Path(path_str)
        if p.exists() or p.parent.exists():
            resolved = p.resolve()
            match_res = re.match(r"^([a-zA-Z]):/(.*)$", str(resolved).replace("\\", "/"))
            if match_res:
                return f"/mnt/{match_res.group(1).lower()}/{match_res.group(2)}"
    except Exception:
        pass
    return path_str


def main():
    translated_args = []
    for arg in sys.argv[1:]:
        if ":" in arg or "\\" in arg or "/" in arg:
            translated_args.append(win_to_wsl_path(arg))
        else:
            translated_args.append(arg)

    cmd = ["wsl", "bcftools"] + translated_args
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
