"""Fase 0 chromosome characterization and 32-bit overflow summary utilities."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any, TextIO

from app.core.config import settings

SIGNED_32BIT_MAX = 2_147_483_647
SUMMARY_HEADER = ("CHROM", "TOTAL_SNP", "MIN_POS", "MAX_POS", "OVERFLOW", "INVALID_POS")


def _is_gzip_file(path: Path) -> bool:
    """Return True when file begins with the gzip magic header bytes."""
    with path.open("rb") as fh:
        return fh.read(2) == b"\x1f\x8b"


def _open_vcf_text(path: Path) -> TextIO:
    """Open a VCF file (plain text or gzip/bgzip) for UTF-8 line iteration."""
    if _is_gzip_file(path):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _build_summary_payload(rows: list[dict[str, Any]], threshold: int) -> dict[str, Any]:
    """Assemble standardized summary totals and chromosome list sorted by name."""
    sorted_rows = sorted(rows, key=lambda row: str(row["chrom"]))
    overflow_chromosomes = [row["chrom"] for row in sorted_rows if row["needs_split"]]
    return {
        "threshold_bp": threshold,
        "total_variants": sum(int(row["total_snp"]) for row in sorted_rows),
        "total_overflow": sum(int(row["overflow_count"]) for row in sorted_rows),
        "total_invalid_pos": sum(int(row["invalid_pos_count"]) for row in sorted_rows),
        "overflow_chromosomes": overflow_chromosomes,
        "needs_split": bool(overflow_chromosomes),
        "chromosomes": sorted_rows,
    }


def summarize_chromosomes(
    vcf_path: str | Path,
    *,
    threshold_bp: int | None = None,
) -> dict[str, Any]:
    """Scan VCF CHROM/POS columns to detect >32-bit overflow and invalid positions."""
    path = Path(vcf_path)
    if not path.is_file():
        raise FileNotFoundError(f"Berkas VCF tidak ditemukan: {path}")

    threshold = int(threshold_bp if threshold_bp is not None else settings.CHROM_SPLIT_THRESHOLD_BP)
    per_chrom: dict[str, dict[str, int]] = {}

    with _open_vcf_text(path) as fh:
        for raw_line in fh:
            if not raw_line or raw_line.startswith("#"):
                continue
            parts = raw_line.rstrip("\r\n").split("\t", 2)
            if len(parts) < 2:
                continue
            chrom = parts[0].strip()
            if not chrom:
                continue
            try:
                pos = int(parts[1])
            except ValueError as exc:
                raise ValueError(
                    f"Posisi VCF tidak valid pada kromosom {chrom!r}: {parts[1]!r}"
                ) from exc

            entry = per_chrom.get(chrom)
            if entry is None:
                entry = {
                    "total_snp": 0,
                    "min_pos": pos,
                    "max_pos": pos,
                    "overflow_count": 0,
                    "invalid_pos_count": 0,
                }
                per_chrom[chrom] = entry

            entry["total_snp"] += 1
            if pos < entry["min_pos"]:
                entry["min_pos"] = pos
            if pos > entry["max_pos"]:
                entry["max_pos"] = pos
            if pos > threshold:
                entry["overflow_count"] += 1
            if pos <= 0:
                entry["invalid_pos_count"] += 1

    rows = [
        {
            "chrom": chrom,
            "total_snp": stats["total_snp"],
            "min_pos": stats["min_pos"],
            "max_pos": stats["max_pos"],
            "overflow_count": stats["overflow_count"],
            "invalid_pos_count": stats["invalid_pos_count"],
            "needs_split": stats["overflow_count"] > 0,
        }
        for chrom, stats in sorted(per_chrom.items(), key=lambda item: item[0])
    ]
    return _build_summary_payload(rows, threshold)


def write_chromosome_summary(summary: dict[str, Any], out_path: str | Path) -> Path:
    """Atomically write chromosome characteristics summary in the Fase 0 TSV format."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(target.suffix + ".tmp")

    lines = ["\t".join(SUMMARY_HEADER)]
    for row in sorted(summary.get("chromosomes", []), key=lambda item: str(item["chrom"])):
        lines.append(
            "\t".join(
                [
                    str(row["chrom"]),
                    str(int(row["total_snp"])),
                    str(int(row["min_pos"])),
                    str(int(row["max_pos"])),
                    str(int(row["overflow_count"])),
                    str(int(row["invalid_pos_count"])),
                ]
            )
        )

    tmp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp_path.replace(target)
    return target


def load_chromosome_summary(
    path: str | Path,
    *,
    threshold_bp: int | None = None,
) -> dict[str, Any] | None:
    """Load chromosome_characteristics_summary.txt into a summary dict, or None if absent."""
    target = Path(path)
    if not target.is_file():
        return None

    threshold = int(threshold_bp if threshold_bp is not None else settings.CHROM_SPLIT_THRESHOLD_BP)
    rows: list[dict[str, Any]] = []
    with target.open("r", encoding="utf-8") as fh:
        for idx, raw_line in enumerate(fh):
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            cols = line.split("\t")
            if idx == 0 and cols[: len(SUMMARY_HEADER)] == list(SUMMARY_HEADER):
                continue
            if len(cols) < 6:
                continue
            overflow_count = int(cols[4])
            rows.append(
                {
                    "chrom": cols[0],
                    "total_snp": int(cols[1]),
                    "min_pos": int(cols[2]),
                    "max_pos": int(cols[3]),
                    "overflow_count": overflow_count,
                    "invalid_pos_count": int(cols[5]),
                    "needs_split": overflow_count > 0,
                }
            )

    return _build_summary_payload(rows, threshold)
