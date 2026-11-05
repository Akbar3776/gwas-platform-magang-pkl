"""bcftools cross-platform wrapper for Windows/WSL environments.

Translates Windows paths (D:\\... or D:/...) into WSL paths (/mnt/d/...)
and executes bcftools inside the WSL environment.
"""

from __future__ import annotations

from pathlib import Path
import re
import shlex
import subprocess
import sys

INT32_MAX_POS = 2_147_483_647



def win_to_wsl_path(path_str: str) -> str:
    """Convert Windows path to WSL path."""
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
    translated_args = []
    for arg in sys.argv[1:]:
        if ":" in arg or "\\" in arg or "/" in arg:
            translated_args.append(win_to_wsl_path(arg))
        else:
            translated_args.append(arg)

    if (
        len(translated_args) >= 2
        and translated_args[0] == "sort"
        and not translated_args[-1].startswith("-")
        and translated_args[-1] != "-"
    ):
        input_wsl = translated_args[-1]
        lower_in = input_wsl.lower()
        if lower_in.endswith((".vcf", ".vcf.gz", ".gz")):
            sort_opts = translated_args[:-1]
            awk_prog = f"/^#/ {{print; next}} ($2 + 0) <= {INT32_MAX_POS} {{print}}"
            if lower_in.endswith(".gz"):
                stream_cmd = f"gzip -dc {shlex.quote(input_wsl)} | awk -F'\\t' {shlex.quote(awk_prog)}"
            else:
                stream_cmd = f"awk -F'\\t' {shlex.quote(awk_prog)} {shlex.quote(input_wsl)}"
            bcf_cmd = "bcftools " + " ".join(shlex.quote(a) for a in sort_opts) + " -"
            bash_cmd = f"set -o pipefail; {stream_cmd} | {bcf_cmd}"
            sys.exit(subprocess.call(["wsl", "--exec", "bash", "-c", bash_cmd]))

    cmd = ["wsl", "--exec", "bcftools"] + translated_args
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    main()
