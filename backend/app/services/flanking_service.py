"""Fase 5.4 and Fase 6 helpers for BIM REF/ALT enrichment, candidate regions, and nearby variants."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import csv
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.annotation_service import merge_annotation_input_snps
from app.services.chromosome_service import _validate_contig_name, restore_coord
from app.tasks._common import chr_label


def _map_snp_engines(
    source_label: str,
    engines_by_method: dict[str, str] | None,
) -> str:
    """Resolve provenance engine label(s) from combined SNP source string."""
    mapping = {str(k).upper(): str(v) for k, v in (engines_by_method or {}).items() if v}
    engines: list[str] = []
    for part in str(source_label or "").split(","):
        token = part.strip().upper()
        if not token:
            continue
        if token in {"GEMMA", "GLM", "LMM"}:
            eng = "gemma"
        else:
            eng = mapping.get(token, "unknown")
        if eng not in engines:
            engines.append(eng)
    return ", ".join(engines) if engines else "gemma"


def load_bim_variants(
    bim_path: str | Path,
    *,
    split_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read PLINK2 .bim rows (col 5=ALT, col 6=REF) and restore original coordinates."""
    path = Path(bim_path)
    if not path.is_file():
        return []

    variants: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            cols = raw_line.strip().split()
            if len(cols) < 6:
                continue
            raw_chr = cols[0].strip()
            snp_id = cols[1].strip()
            try:
                raw_pos = int(float(cols[3]))
            except ValueError:
                continue
            alt_allele = cols[4].strip().upper()
            ref_allele = cols[5].strip().upper()
            orig_chr, orig_pos = restore_coord(raw_chr, raw_pos, split_map)
            variants.append({
                "snp_id": snp_id,
                "pseudo_chr": raw_chr,
                "pseudo_pos": raw_pos,
                "orig_chr": orig_chr,
                "orig_pos": orig_pos,
                "alt": alt_allele,
                "ref": ref_allele,
            })
    return variants


def collect_significant_snps(
    lead_snps: list[dict[str, Any]] | None,
    multilocus_snps: list[dict[str, Any]] | None = None,
    *,
    engines_by_method: dict[str, str] | None = None,
    p_threshold: float | None = None,
    split_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Combine single-locus and significant multi-locus SNPs with restored coords and engine labels."""
    pre_engine_by_key: dict[tuple[str, int], str] = {}
    for item in (*(lead_snps or []), *(multilocus_snps or [])):
        if not isinstance(item, dict) or not item.get("engine"):
            continue
        k_chr = chr_label(str(item.get("orig_chr") or item.get("chr") or ""))
        k_pos = int(item.get("orig_pos") or item.get("pos") or 0)
        if k_chr and k_pos > 0:
            pre_engine_by_key[(k_chr, k_pos)] = str(item["engine"])

    merged = merge_annotation_input_snps(
        gemma_snps=lead_snps,
        multilocus_snps=multilocus_snps,
        p_threshold=p_threshold,
    )
    for item in merged:
        pseudo_chr = str(item.get("pseudo_chr") or item.get("chr") or "").strip()
        pseudo_pos = int(item.get("pseudo_pos") or item.get("pos") or 0)
        if split_map is not None:
            orig_chr, orig_pos = restore_coord(pseudo_chr, pseudo_pos, split_map)
        elif item.get("orig_chr") and item.get("orig_pos"):
            orig_chr = str(item["orig_chr"]).strip()
            orig_pos = int(item["orig_pos"])
        else:
            orig_chr, orig_pos = pseudo_chr, pseudo_pos
        item["pseudo_chr"] = pseudo_chr
        item["pseudo_pos"] = pseudo_pos
        item["orig_chr"] = orig_chr
        item["orig_pos"] = orig_pos
        item["chr"] = orig_chr
        item["pos"] = orig_pos

        if not item.get("engine"):
            pre_eng = pre_engine_by_key.get((chr_label(orig_chr), orig_pos))
            item["engine"] = pre_eng or _map_snp_engines(item.get("source") or "", engines_by_method)
    return merged


def attach_ref_alt_from_bim(
    snps: list[dict[str, Any]],
    bim_path: str | Path | None,
    *,
    split_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Populate REF (col 6) and ALT (col 5) from PLINK .bim across all significant SNPs."""
    if not snps or not bim_path:
        return snps
    bim_variants = load_bim_variants(bim_path, split_map=split_map)
    if not bim_variants:
        return snps

    by_id: dict[str, dict[str, Any]] = {}
    by_pseudo: dict[tuple[str, int], dict[str, Any]] = {}
    by_orig: dict[tuple[str, int], dict[str, Any]] = {}
    for var in bim_variants:
        snp_id = var["snp_id"]
        if snp_id and snp_id != ".":
            by_id.setdefault(snp_id, var)
        by_pseudo.setdefault((chr_label(var["pseudo_chr"]), int(var["pseudo_pos"])), var)
        by_orig.setdefault((chr_label(var["orig_chr"]), int(var["orig_pos"])), var)

    invalid_alleles = {"", ".", "0"}
    for item in snps:
        pseudo_chr = str(item.get("pseudo_chr") or item.get("chr") or "")
        pseudo_pos = int(item.get("pseudo_pos") or item.get("pos") or 0)
        orig_chr = str(item.get("orig_chr") or item.get("chr") or "")
        orig_pos = int(item.get("orig_pos") or item.get("pos") or 0)
        snp_id = str(item.get("snp") or "").strip()

        matched = (
            by_pseudo.get((chr_label(pseudo_chr), pseudo_pos))
            or by_orig.get((chr_label(orig_chr), orig_pos))
            or (by_id.get(snp_id) if snp_id else None)
        )
        if matched is None:
            continue
        if not item.get("ref") and matched["ref"] not in invalid_alleles:
            item["ref"] = matched["ref"]
        if not item.get("alt") and matched["alt"] not in invalid_alleles:
            item["alt"] = matched["alt"]
    return snps


def write_candidate_regions(
    snps: list[dict[str, Any]],
    window_bp: int,
    csv_path: str | Path,
    bed_path: str | Path,
) -> dict[str, Any]:
    """Write candidate region CSV and 0-based BED sorted by ascending p-value."""
    win = max(0, int(window_bp))
    ordered = sorted(
        snps,
        key=lambda item: float(item["p"]) if item.get("p") is not None else 1.0,
    )

    csv_target = Path(csv_path)
    bed_target = Path(bed_path)
    csv_target.parent.mkdir(parents=True, exist_ok=True)
    bed_target.parent.mkdir(parents=True, exist_ok=True)
    csv_tmp = csv_target.with_suffix(csv_target.suffix + ".tmp")
    bed_tmp = bed_target.with_suffix(bed_target.suffix + ".tmp")

    csv_rows: list[list[Any]] = []
    bed_lines: list[str] = []
    total_bp = 0

    for item in ordered:
        chrom = str(item.get("orig_chr") or item.get("chr") or "").strip()
        pos = int(item.get("orig_pos") or item.get("pos") or 0)
        if not chrom or pos <= 0:
            continue
        _validate_contig_name(chrom)
        snp_id = str(item.get("snp") or f"{chrom}:{pos}").strip()
        flank_start_1 = max(1, pos - win)
        bed_start0 = max(0, pos - win - 1)
        flank_end = pos + win
        total_bp += max(0, flank_end - bed_start0)

        csv_rows.append([
            snp_id,
            chrom,
            pos,
            str(item.get("ref") or ""),
            str(item.get("alt") or ""),
            flank_start_1,
            flank_end,
            item.get("p"),
            str(item.get("source") or item.get("snp_source") or "GEMMA"),
            str(item.get("engine") or "gemma"),
        ])
        bed_lines.append(f"{chrom}\t{bed_start0}\t{flank_end}\t{snp_id}")

    with csv_tmp.open("w", encoding="utf-8", newline="") as cf:
        writer = csv.writer(cf)
        writer.writerow([
            "SNP",
            "Chromosome_GFF",
            "True_Pos",
            "REF",
            "ALT",
            "Flanking_Start",
            "Flanking_End",
            "P.value",
            "Source",
            "Engine",
        ])
        writer.writerows(csv_rows)
    csv_tmp.replace(csv_target)

    bed_tmp.write_text(("\n".join(bed_lines) + "\n") if bed_lines else "", encoding="utf-8")
    bed_tmp.replace(bed_target)

    return {
        "csv_path": str(csv_target),
        "bed_path": str(bed_target),
        "n_regions": len(csv_rows),
        "total_bp": total_bp,
        "window_bp": win,
    }


def extract_regions_fasta(
    bed_path: str | Path,
    fasta_path: str | Path | None,
    out_fa: str | Path,
    *,
    max_total_bp: int | None = None,
) -> dict[str, Any]:
    """Extract FASTA sequences for candidate BED intervals with a strict total bp budget."""
    from app.services.primer_service import _lookup_contig, _read_fasta_region, load_fasta_index

    max_bp = int(max_total_bp if max_total_bp is not None else settings.CANDIDATE_REGION_MAX_TOTAL_BP)
    if fasta_path is None or not Path(fasta_path).is_file():
        return {
            "status": "no_reference",
            "reason": "FASTA referensi tidak tersedia",
            "written_regions": 0,
            "truncated": False,
            "truncated_count": 0,
            "total_bp": 0,
            "max_total_bp": max_bp,
            "fasta_path": None,
        }

    bed_file = Path(bed_path)
    if not bed_file.is_file():
        return {
            "status": "empty",
            "reason": "Berkas BED region kandidat tidak ditemukan",
            "written_regions": 0,
            "truncated": False,
            "truncated_count": 0,
            "total_bp": 0,
            "max_total_bp": max_bp,
            "fasta_path": None,
        }

    bed_entries: list[tuple[str, int, int, str]] = []
    for raw_line in bed_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) < 4:
            continue
        chrom = _validate_contig_name(cols[0].strip())
        bed_entries.append((chrom, max(0, int(cols[1])), int(cols[2]), cols[3].strip()))

    fasta_file = Path(fasta_path)
    fasta_index = load_fasta_index(fasta_file)

    fasta_blocks: list[str] = []
    total_bp = 0
    written_regions = 0
    truncated = False
    truncated_count = 0

    for idx, (chrom, start0, end_req, snp_name) in enumerate(bed_entries):
        rec = _lookup_contig(fasta_index, chrom)
        if rec is None:
            continue
        end_clamped = min(end_req, int(rec["length"]))
        region_len = end_clamped - start0
        if region_len <= 0:
            continue

        if total_bp + region_len > max_bp:
            truncated = True
            truncated_count = len(bed_entries) - idx
            break

        seq = _read_fasta_region(fasta_file, chrom, start0 + 1, end_clamped, index=fasta_index) or ""
        if not seq:
            continue

        header = f">{snp_name}::{chrom}:{start0}-{end_clamped}"
        wrapped = [seq[i:i + 80] for i in range(0, len(seq), 80)]
        fasta_blocks.append("\n".join([header, *wrapped]))
        total_bp += len(seq)
        written_regions += 1

    out_target = Path(out_fa)
    if written_regions == 0:
        return {
            "status": "empty",
            "reason": "Tidak ada region kandidat yang cocok dengan kontig FASTA" if bed_entries else "Daftar region kosong",
            "written_regions": 0,
            "truncated": truncated,
            "truncated_count": truncated_count,
            "total_bp": 0,
            "max_total_bp": max_bp,
            "fasta_path": None,
        }

    out_target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_target.with_suffix(out_target.suffix + ".tmp")
    tmp_path.write_text("\n".join(fasta_blocks) + "\n", encoding="utf-8")
    tmp_path.replace(out_target)

    return {
        "status": "completed",
        "reason": None,
        "written_regions": written_regions,
        "truncated": truncated,
        "truncated_count": truncated_count,
        "total_bp": total_bp,
        "max_total_bp": max_bp,
        "fasta_path": str(out_target),
    }


def build_bim_position_index(
    bim_path: str | Path,
    *,
    split_map: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Index .bim variants sorted by original position per normalized chromosome for range lookup."""
    variants = load_bim_variants(bim_path, split_map=split_map)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for var in variants:
        grouped.setdefault(chr_label(var["orig_chr"]), []).append(var)

    index: dict[str, dict[str, Any]] = {}
    for norm_chr, items in grouped.items():
        items.sort(key=lambda item: int(item["orig_pos"]))
        index[norm_chr] = {
            "positions": [int(item["orig_pos"]) for item in items],
            "variants": items,
        }
    return index


def find_nearby_variants(
    chrom: str,
    pos: int,
    flank_bp: int,
    bim_or_index: str | Path | dict[str, Any] | None,
    *,
    split_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Find other BIM variants within +/- flank_bp around (chrom, pos) in original coordinates."""
    if bim_or_index is None or int(flank_bp) <= 0:
        return []
    if isinstance(bim_or_index, dict):
        pos_index = bim_or_index
    else:
        pos_index = build_bim_position_index(bim_or_index, split_map=split_map)

    orig_chr, orig_pos = restore_coord(chrom, int(pos), split_map)
    bucket = pos_index.get(chr_label(orig_chr))
    if not bucket or orig_pos <= 0:
        return []

    positions: list[int] = bucket["positions"]
    variants: list[dict[str, Any]] = bucket["variants"]
    win = int(flank_bp)
    lo = bisect_left(positions, orig_pos - win)
    hi = bisect_right(positions, orig_pos + win)

    nearby: list[dict[str, Any]] = []
    for var in variants[lo:hi]:
        v_pos = int(var["orig_pos"])
        if v_pos == orig_pos:
            continue
        nearby.append({
            "pos": v_pos,
            "ref": var["ref"],
            "alt": var["alt"],
            "offset": v_pos - orig_pos,
            "snp_id": var["snp_id"],
        })
    return nearby


KASP_MIN_FLANK_BP = 60
_ACGT_BASES = {"A", "C", "G", "T"}


def build_kasp_input(
    snps: list[dict[str, Any]],
    fasta_path: str | Path | None,
    bim_path: str | Path | None = None,
    *,
    flank_bp: int | None = None,
    split_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build EasyKASP input rows (<left>[REF/ALT]<right>) with N-masked nearby variants."""
    from app.services.primer_service import _lookup_contig, _read_fasta_region, load_fasta_index

    flank_len = int(flank_bp if flank_bp is not None else settings.KASP_FLANK_BP)
    has_fasta = fasta_path is not None and Path(fasta_path).is_file()
    if not has_fasta:
        return [
            {
                "snp_id": str(s.get("snp") or f"{s.get('orig_chr') or s.get('chr')}:{s.get('orig_pos') or s.get('pos')}").strip(),
                "allele_1": str(s.get("ref") or "").strip().upper(),
                "allele_2": str(s.get("alt") or "").strip().upper(),
                "flanking": "",
                "orig_chr": str(s.get("orig_chr") or s.get("chr") or ""),
                "orig_pos": int(s.get("orig_pos") or s.get("pos") or 0),
                "fasta_ref": "",
                "masked_neighbors": 0,
                "left_len": 0,
                "right_len": 0,
                "status": "no_reference",
                "reason": "FASTA referensi tidak tersedia",
            }
            for s in snps
        ]

    fasta_file = Path(fasta_path)
    fasta_index = load_fasta_index(fasta_file)
    bim_index = (
        build_bim_position_index(bim_path, split_map=split_map)
        if bim_path and Path(bim_path).is_file()
        else None
    )

    out_rows: list[dict[str, Any]] = []
    for s in snps:
        orig_chr, orig_pos = restore_coord(
            str(s.get("orig_chr") or s.get("chr") or ""),
            int(s.get("orig_pos") or s.get("pos") or 0),
            split_map,
        )
        snp_id = str(s.get("snp") or f"{orig_chr}:{orig_pos}").strip()
        ref = str(s.get("ref") or "").strip().upper()
        alt = str(s.get("alt") or "").strip().upper()

        base_row: dict[str, Any] = {
            "snp_id": snp_id,
            "allele_1": ref,
            "allele_2": alt,
            "flanking": "",
            "orig_chr": orig_chr,
            "orig_pos": orig_pos,
            "fasta_ref": "",
            "masked_neighbors": 0,
            "left_len": 0,
            "right_len": 0,
        }

        if ref not in _ACGT_BASES or alt not in _ACGT_BASES or ref == alt:
            out_rows.append({
                **base_row,
                "status": "unsupported_variant",
                "reason": "Hanya SNP biallelik satu basa (A/C/G/T) yang didukung untuk desain KASP",
            })
            continue

        rec = _lookup_contig(fasta_index, orig_chr)
        if rec is None:
            out_rows.append({
                **base_row,
                "status": "no_reference_for_chr",
                "reason": f"Kontig '{orig_chr}' tidak ditemukan pada FASTA referensi",
            })
            continue

        contig_len = int(rec["length"])
        left_avail = orig_pos - 1
        right_avail = contig_len - orig_pos
        if orig_pos <= 0 or orig_pos > contig_len or left_avail < KASP_MIN_FLANK_BP or right_avail < KASP_MIN_FLANK_BP:
            out_rows.append({
                **base_row,
                "status": "flank_too_short",
                "reason": f"Flanking di sekitar {orig_chr}:{orig_pos} kurang dari {KASP_MIN_FLANK_BP} bp",
            })
            continue

        start_1 = max(1, orig_pos - flank_len)
        end_1 = min(contig_len, orig_pos + flank_len)
        seq = _read_fasta_region(fasta_file, orig_chr, start_1, end_1, index=fasta_index) or ""
        center_idx = orig_pos - start_1
        if not (0 <= center_idx < len(seq)):
            out_rows.append({
                **base_row,
                "status": "flank_too_short",
                "reason": f"Gagal mengekstrak posisi {orig_chr}:{orig_pos} dari FASTA",
            })
            continue

        fasta_ref = seq[center_idx]
        if fasta_ref != ref:
            out_rows.append({
                **base_row,
                "fasta_ref": fasta_ref,
                "status": "ref_mismatch",
                "reason": f"Basa FASTA '{fasta_ref}' != REF '{ref}' pada {orig_chr}:{orig_pos}",
            })
            continue

        left_chars = list(seq[:center_idx])
        right_chars = list(seq[center_idx + 1:])
        masked_neighbors = 0
        if bim_index is not None:
            nearby = find_nearby_variants(orig_chr, orig_pos, flank_len, bim_index, split_map=split_map)
            for nv in nearby:
                off = int(nv["offset"])
                if off < 0:
                    idx_l = len(left_chars) + off
                    if 0 <= idx_l < len(left_chars):
                        left_chars[idx_l] = "N"
                        masked_neighbors += 1
                elif off > 0:
                    idx_r = off - 1
                    if 0 <= idx_r < len(right_chars):
                        right_chars[idx_r] = "N"
                        masked_neighbors += 1

        left_str = "".join(left_chars)
        right_str = "".join(right_chars)
        out_rows.append({
            **base_row,
            "flanking": f"{left_str}[{ref}/{alt}]{right_str}",
            "fasta_ref": fasta_ref,
            "masked_neighbors": masked_neighbors,
            "left_len": len(left_chars),
            "right_len": len(right_chars),
            "status": "ok",
            "reason": None,
        })

    return out_rows
