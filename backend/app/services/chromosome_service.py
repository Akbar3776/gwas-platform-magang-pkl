"""Fase 0 chromosome characterization and 32-bit overflow split/restore helpers."""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
import re
from typing import Any, TextIO
import uuid

from app.core.config import settings
from app.tasks._common import chr_label, read_json, write_json

logger = logging.getLogger(__name__)

SIGNED_32BIT_MAX = 2_147_483_647
SUMMARY_HEADER = ("CHROM", "TOTAL_SNP", "MIN_POS", "MAX_POS", "OVERFLOW", "INVALID_POS")
_CONTIG_NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_CONTIG_ID_RE = re.compile(r"(?:^|,)ID=([^,>]+)")
_CONTIG_LEN_RE = re.compile(r"(?:^|,)length=(\d+)")
_SPLIT_PART_RE = re.compile(r"^.+_p\d+$")


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


def _validate_contig_name(chrom: str) -> str:
    """Validate that contig identifiers conform to safe VCF/CLI characters."""
    if not _CONTIG_NAME_RE.fullmatch(chrom):
        raise ValueError(
            f"Nama contig tidak valid untuk pemecahan kromosom: {chrom!r} "
            "(harus cocok dengan ^[A-Za-z0-9_.:-]+$)"
        )
    return chrom


def split_map_path(job_id: str | uuid.UUID) -> Path:
    """Return canonical path for normalization/chrom_split_map.json of a job."""
    return Path(settings.STORAGE_ROOT) / "jobs" / str(job_id) / "normalization" / "chrom_split_map.json"


def save_split_map(job_id: str | uuid.UUID, split_map: dict[str, Any]) -> Path:
    """Atomically persist chrom_split_map.json under the job's normalization stage."""
    target = split_map_path(job_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json(target, split_map)
    return target


def load_split_map(job_id: str | uuid.UUID | None) -> dict[str, Any] | None:
    """Load chrom_split_map.json if present for job_id, else return None."""
    if not job_id:
        return None
    target = split_map_path(job_id)
    if not target.is_file():
        return None
    try:
        payload = read_json(target)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def restore_coord(
    chrom: str | None,
    pos: int | None,
    split_map: dict[str, Any] | None,
) -> tuple[str, int]:
    """Restore a pseudo-chromosome coordinate (e.g. chr7_p2 or 7_p2) to original coordinates."""
    raw_chr = "" if chrom is None else str(chrom).strip()
    raw_pos = int(pos or 0)
    if not split_map or not raw_chr:
        return (raw_chr, raw_pos)

    parts = split_map.get("parts")
    if not isinstance(parts, dict) or not parts:
        return (raw_chr, raw_pos)

    entry = parts.get(raw_chr)
    if entry is None and _SPLIT_PART_RE.fullmatch(raw_chr):
        target_lbl = chr_label(raw_chr)
        for key, candidate in parts.items():
            if chr_label(str(key)) == target_lbl:
                entry = candidate
                break

    if entry is None:
        if _SPLIT_PART_RE.fullmatch(raw_chr):
            logger.warning(
                "[WARN] pemulihan koordinat: pseudo-kromosom %s tidak ditemukan pada chrom_split_map",
                raw_chr,
            )
        return (raw_chr, raw_pos)

    orig_chr = str(entry["orig_chr"])
    offset_bp = int(entry.get("offset_bp") or 0)
    restored_pos = offset_bp + raw_pos if raw_pos > 0 else raw_pos
    return (orig_chr, restored_pos)


def _scan_vcf_contig_lengths(path: Path) -> dict[str, int]:
    """Read ##contig lengths from the VCF header before #CHROM."""
    lengths: dict[str, int] = {}
    with _open_vcf_text(path) as fh:
        for raw_line in fh:
            if raw_line.startswith("#CHROM"):
                break
            if not raw_line.startswith("##contig=<"):
                continue
            inner = raw_line.strip()[len("##contig=<"):-1]
            id_match = _CONTIG_ID_RE.search(inner)
            if not id_match:
                continue
            contig_id = id_match.group(1).strip()
            len_match = _CONTIG_LEN_RE.search(inner)
            if len_match:
                lengths[contig_id] = int(len_match.group(1))
    return lengths


def _build_split_plan(
    summary: dict[str, Any],
    contig_lengths: dict[str, int],
    unit_bp: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], dict[str, Any]]:
    """Build per-chromosome pseudo-contig layout for chromosomes exceeding threshold."""
    split_specs: dict[str, list[dict[str, Any]]] = {}
    split_chroms_meta: dict[str, Any] = {}
    parts_map: dict[str, Any] = {}

    for row in summary.get("chromosomes", []):
        orig_chr = _validate_contig_name(str(row["chrom"]))
        if not row.get("needs_split"):
            continue
        effective_len = max(int(contig_lengths.get(orig_chr, 0)), int(row["max_pos"]), 1)
        num_parts = ((effective_len - 1) // unit_bp) + 1
        part_entries: list[dict[str, Any]] = []
        for part in range(num_parts):
            pseudo_chr = _validate_contig_name(f"{orig_chr}_p{part}")
            offset_bp = part * unit_bp
            part_len = min(unit_bp, effective_len - offset_bp)
            info = {
                "orig_chr": orig_chr,
                "part": part,
                "pseudo_chr": pseudo_chr,
                "offset_bp": offset_bp,
                "length": max(1, int(part_len)),
            }
            part_entries.append(info)
            parts_map[pseudo_chr] = {
                "orig_chr": orig_chr,
                "part": part,
                "offset_bp": offset_bp,
            }
        split_specs[orig_chr] = part_entries
        split_chroms_meta[orig_chr] = {
            "max_orig_pos": int(row["max_pos"]),
            "effective_len": effective_len,
            "num_parts": num_parts,
            "parts": [entry["pseudo_chr"] for entry in part_entries],
        }

    return split_specs, split_chroms_meta, parts_map


def split_overflow_chromosomes(
    input_vcf: str | Path,
    output_vcf: str | Path,
    *,
    summary: dict[str, Any] | None = None,
    threshold_bp: int | None = None,
    unit_bp: int | None = None,
) -> dict[str, Any]:
    """Split overflowing chromosomes into <chrom>_p<k> pseudo-contigs and filter POS <= 0."""
    in_path = Path(input_vcf)
    if not in_path.is_file():
        raise FileNotFoundError(f"Berkas VCF input tidak ditemukan: {in_path}")

    threshold = int(threshold_bp if threshold_bp is not None else settings.CHROM_SPLIT_THRESHOLD_BP)
    unit = int(unit_bp if unit_bp is not None else settings.CHROM_SPLIT_UNIT_BP)
    if unit <= 0:
        raise ValueError(f"unit_bp harus > 0, diterima {unit}")
    if threshold < unit:
        raise ValueError(f"threshold_bp ({threshold}) harus >= unit_bp ({unit})")

    chr_summary = summary if summary is not None else summarize_chromosomes(in_path, threshold_bp=threshold)
    contig_lengths = _scan_vcf_contig_lengths(in_path)
    split_specs, split_chroms_meta, parts_map = _build_split_plan(chr_summary, contig_lengths, unit)

    out_path = Path(output_vcf)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")

    emitted_split_contigs: set[str] = set()
    invalid_pos_removed = 0
    kept_variants = 0

    with _open_vcf_text(in_path) as src, tmp_path.open("w", encoding="utf-8", newline="\n") as dst:
        for raw_line in src:
            line = raw_line.rstrip("\r\n")
            if line.startswith("##contig=<"):
                inner = line[len("##contig=<"):-1]
                id_match = _CONTIG_ID_RE.search(inner)
                contig_id = id_match.group(1).strip() if id_match else ""
                if contig_id in split_specs:
                    if contig_id not in emitted_split_contigs:
                        for entry in split_specs[contig_id]:
                            dst.write(f"##contig=<ID={entry['pseudo_chr']},length={entry['length']}>\n")
                        emitted_split_contigs.add(contig_id)
                    continue
                dst.write(line + "\n")
                continue

            if line.startswith("#CHROM"):
                for orig_chr, entries in split_specs.items():
                    if orig_chr not in emitted_split_contigs:
                        for entry in entries:
                            dst.write(f"##contig=<ID={entry['pseudo_chr']},length={entry['length']}>\n")
                        emitted_split_contigs.add(orig_chr)
                dst.write(line + "\n")
                continue

            if line.startswith("#") or not line:
                if line:
                    dst.write(line + "\n")
                continue

            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            chrom = _validate_contig_name(parts[0].strip())
            pos = int(parts[1])
            if pos <= 0:
                invalid_pos_removed += 1
                continue

            kept_variants += 1
            if chrom in split_specs:
                part = (pos - 1) // unit
                pseudo_chr = f"{chrom}_p{part}"
                pseudo_pos = pos - (part * unit)
                dst.write(f"{pseudo_chr}\t{pseudo_pos}\t{parts[2]}\n")
            else:
                dst.write(line + "\n")

    tmp_path.replace(out_path)

    return {
        "threshold_bp": threshold,
        "unit_bp": unit,
        "split_applied": bool(split_specs),
        "overflow_chromosomes": sorted(split_specs.keys()),
        "total_variants": int(chr_summary["total_variants"]),
        "kept_variants": kept_variants,
        "invalid_pos_removed": invalid_pos_removed,
        "overflow_removed": 0,
        "summary": chr_summary,
        "split_map": {
            "threshold_bp": threshold,
            "unit_bp": unit,
            "split_chroms": split_chroms_meta,
            "parts": parts_map,
        },
    }
