"""Utilitas bersama validasi dan normalisasi ID gen lintas modul GO dan ortolog."""

from __future__ import annotations

import re

GENE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:|-]{0,127}$")
