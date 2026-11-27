"""Utilitas bersama validasi dan normalisasi ID gen lintas modul GO dan ortolog."""

from __future__ import annotations

import re

GENE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:|-]{0,127}$")
_ISOFORM_SUFFIX_RE = re.compile(r"\.\d+$")


def strip_isoform_suffix(key: str) -> str:
    """Menghapus satu sufiks isoform numerik (.N) di akhir ID gen/transkrip."""
    return _ISOFORM_SUFFIX_RE.sub("", str(key or "").strip())
