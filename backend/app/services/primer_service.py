"""
Service untuk Fase 5.4 / Fase 6–8:
1. Fase 6: Ekstraksi sekuens flanking 201 bp (100 bp upstream + [Alel] + 100 bp downstream)
   menggunakan file FASTA referensi terindeks `.fai` (samtools faidx).
2. Fase 7: Desain pasangan primer PCR otomatis (LEFT PRIMER, RIGHT PRIMER,
   start, len, tm, gc%, any_th, 3'_th, hairpin, seq, PRODUCT SIZE, PAIR ANY_TH COMPL, PAIR 3'_TH COMPL).
3. Fase 8: Evaluasi struktur sekunder (self-dimer, 3' dimer, hairpin).
"""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path
import re
from typing import Any

from app.tasks._common import chr_label


def _allow_synthetic_annotation() -> bool:
    return os.getenv("ALLOW_SYNTHETIC_ANNOTATION", "").strip().lower() in ("1", "true", "yes")


# Parameter termodinamika Nearest-Neighbor SantaLucia (1998) (dH dalam cal/mol, dS dalam cal/(K·mol))
NN_PARAMS: dict[str, tuple[float, float]] = {
    "AA": (-7900.0, -22.2),
    "TT": (-7900.0, -22.2),
    "AT": (-7200.0, -20.4),
    "TA": (-7200.0, -21.3),
    "CA": (-8500.0, -22.7),
    "TG": (-8500.0, -22.7),
    "GT": (-8400.0, -22.4),
    "AC": (-8400.0, -22.4),
    "CT": (-7800.0, -21.0),
    "AG": (-7800.0, -21.0),
    "GA": (-8200.0, -22.2),
    "TC": (-8200.0, -22.2),
    "CG": (-10600.0, -27.2),
    "GC": (-9800.0, -24.4),
    "GG": (-8000.0, -19.9),
    "CC": (-8000.0, -19.9),
}

COMPLEMENT_MAP = str.maketrans("ACGTacgtNn", "TGCAtgcaNn")


def reverse_complement(seq: str) -> str:
    """Menghasilkan sekuens komplemen terbalik (5' -> 3')."""
    return seq.translate(COMPLEMENT_MAP)[::-1]


def calculate_gc_percent(seq: str) -> float:
    """Menghitung persentase GC dari oligo."""
    if not seq:
        return 0.0
    s = seq.upper()
    gc_count = s.count("G") + s.count("C")
    return round((gc_count / len(s)) * 100.0, 2)


def calculate_tm_santalucia(
    seq: str,
    mv_conc_mm: float = 50.0,
    dv_conc_mm: float = 1.5,
    dntp_mm: float = 0.2,
    oligo_nm: float = 250.0,
) -> float:
    """
    Menghitung suhu leleh (Tm dalam °C) menggunakan rumus Nearest-Neighbor SantaLucia (1998).
    """
    s = seq.upper()
    n = len(s)
    if n < 2:
        return 0.0

    dh = 0.0
    ds = 0.0
    if s[0] in ("G", "C"):
        dh += 100.0
        ds += -2.8
    else:
        dh += 2300.0
        ds += 4.1
    if s[-1] in ("G", "C"):
        dh += 100.0
        ds += -2.8
    else:
        dh += 2300.0
        ds += 4.1

    for i in range(n - 1):
        dinuc = s[i:i + 2]
        step_dh, step_ds = NN_PARAMS.get(dinuc, (-8000.0, -21.5))
        dh += step_dh
        ds += step_ds

    free_divalent = max(0.0, dv_conc_mm - dntp_mm)
    monovalent_eq = (mv_conc_mm + 120.0 * math.sqrt(free_divalent)) / 1000.0
    monovalent_eq = max(monovalent_eq, 1e-5)
    ds_salt = ds + 0.368 * (n - 1) * math.log(monovalent_eq)

    r_gas = 1.9872
    ct = (oligo_nm * 1e-9) / 4.0
    tm_kelvin = dh / (ds_salt + r_gas * math.log(ct))
    return round(tm_kelvin - 273.15, 2)


def _score_secondary_structure(seq1: str, seq2: str | None = None, three_prime_only: bool = False) -> float:
    """Mengevaluasi potensi dimer/hairpin sederhana (skor TH dalam °C, 0.00 jika tidak signifikan)."""
    s1 = seq1.upper()
    s2_rc = reverse_complement(seq2.upper() if seq2 is not None else s1)
    if three_prime_only:
        tail = s1[-6:]
        if tail in s2_rc:
            return round(12.40 + 2.15 * (tail.count("G") + tail.count("C")), 2)
        if s1[-5:] in s2_rc and (s1[-5:].count("G") + s1[-5:].count("C")) >= 3:
            return round(6.10 + 1.45 * (s1[-5:].count("G") + s1[-5:].count("C")), 2)
        return 0.0

    for k in (6, 5):
        for i in range(len(s1) - k + 1):
            sub = s1[i:i + k]
            if sub in s2_rc:
                gc = sub.count("G") + sub.count("C")
                if k >= 6 or gc >= 3:
                    return round(4.5 * k + 2.2 * gc - 16.0, 2)
    return 0.0


def build_fasta_index(fasta_path: str | Path) -> Path:
    """
    Membangun indeks `.fai` berformat samtools faidx (`NAME\tLENGTH\tOFFSET\tLINEBASES\tLINEWIDTH`)
    untuk pembacaan byte-offset acak cepat tanpa memuat file FASTA ke RAM.
    """
    path = Path(fasta_path)
    if not path.is_file():
        raise FileNotFoundError(f"File FASTA tidak ditemukan: {path}")

    entries: list[tuple[str, int, int, int, int]] = []
    cur_name: str | None = None
    cur_len = 0
    cur_offset = 0
    line_bases = 0
    line_width = 0
    saw_short_line = False

    def _flush() -> None:
        if cur_name is not None:
            entries.append((cur_name, cur_len, cur_offset, max(line_bases, 1), max(line_width, 1)))

    with path.open("rb") as fh:
        while True:
            line_start = fh.tell()
            raw_line = fh.readline()
            if not raw_line:
                break
            if raw_line.startswith(b">"):
                _flush()
                header_token = raw_line[1:].strip().split()[0].decode("utf-8", errors="replace")
                cur_name = header_token
                cur_len = 0
                cur_offset = fh.tell()
                line_bases = 0
                line_width = 0
                saw_short_line = False
                continue

            if cur_name is None:
                continue

            raw_len = len(raw_line)
            seq_bytes = raw_line.rstrip(b"\r\n")
            seq_len = len(seq_bytes)
            if seq_len == 0:
                if cur_len > 0:
                    saw_short_line = True
                continue

            if saw_short_line:
                raise ValueError(
                    f"Panjang baris FASTA tidak konsisten sebelum akhir contig '{cur_name}'; gagal membangun .fai."
                )

            if line_bases == 0:
                cur_offset = line_start
                line_bases = seq_len
                line_width = raw_len
            elif seq_len != line_bases or raw_len != line_width:
                if seq_len < line_bases:
                    saw_short_line = True
                else:
                    raise ValueError(
                        f"Panjang baris melebihi LINEBASES pada contig '{cur_name}'; gagal membangun .fai."
                    )

            cur_len += seq_len

    _flush()

    fai_path = Path(f"{path}.fai")
    tmp_fai = Path(f"{path}.fai.tmp")
    lines = [f"{name}\t{length}\t{offset}\t{lb}\t{lw}" for name, length, offset, lb, lw in entries]
    tmp_fai.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp_fai.replace(fai_path)
    return fai_path


def load_fasta_index(fasta_path: str | Path) -> dict[str, Any]:
    """Memuat indeks `.fai` (atau membangunnya sekali bila belum tersedia) beserta peta normalisasi `chr_label`."""
    path = Path(fasta_path)
    if not path.is_file():
        raise FileNotFoundError(f"File FASTA tidak ditemukan: {path}")

    fai_path = Path(f"{path}.fai")
    if not fai_path.is_file() or fai_path.stat().st_mtime < path.stat().st_mtime:
        build_fasta_index(path)

    by_exact: dict[str, dict[str, Any]] = {}
    by_norm: dict[str, dict[str, Any]] = {}
    contigs: list[dict[str, Any]] = []

    with fai_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) < 5:
                continue
            name = parts[0]
            rec = {
                "name": name,
                "length": int(parts[1]),
                "offset": int(parts[2]),
                "line_bases": int(parts[3]),
                "line_width": int(parts[4]),
            }
            by_exact[name] = rec
            by_norm.setdefault(chr_label(name), rec)
            contigs.append({"name": name, "norm_chr": chr_label(name), "length": rec["length"]})

    return {"by_exact": by_exact, "by_norm": by_norm, "contigs": contigs}


def _lookup_contig(index: dict[str, Any], target_chr: str) -> dict[str, Any] | None:
    raw = str(target_chr or "").strip()
    by_exact = index.get("by_exact") or {}
    if raw in by_exact:
        return by_exact[raw]
    norm = chr_label(raw)
    return (index.get("by_norm") or {}).get(norm)


def _read_fasta_region(
    fasta_path: Path,
    target_chr: str,
    start_1based: int,
    end_1based: int,
    *,
    index: dict[str, Any] | None = None,
) -> str | None:
    """Membaca rentang basa `[start_1based, end_1based]` via seek byte-offset `.fai` secara O(1)."""
    idx = index if index is not None else load_fasta_index(fasta_path)
    rec = _lookup_contig(idx, target_chr)
    if rec is None:
        return None

    contig_len = rec["length"]
    s0 = max(0, int(start_1based) - 1)
    e0 = min(contig_len, int(end_1based))
    if s0 >= e0 or contig_len <= 0:
        return ""

    lb = rec["line_bases"]
    lw = rec["line_width"]
    offset = rec["offset"]

    byte_start = offset + (s0 // lb) * lw + (s0 % lb)
    last_0 = e0 - 1
    byte_end = offset + (last_0 // lb) * lw + (last_0 % lb) + 1

    with Path(fasta_path).open("rb") as fh:
        fh.seek(byte_start)
        raw_bytes = fh.read(max(0, byte_end - byte_start))

    return raw_bytes.replace(b"\r", b"").replace(b"\n", b"").decode("ascii", errors="replace").upper()


def _extract_flanking_detail(
    *,
    chr_name: str,
    pos: int,
    allele: str = "C",
    ref_allele: str = "",
    alt_allele: str = "",
    flank_len: int = 100,
    fasta_path: str | Path | None = None,
    fasta_index: dict[str, Any] | None = None,
    allow_synthetic: bool | None = None,
) -> dict[str, Any]:
    if allow_synthetic is None:
        allow_synthetic = _allow_synthetic_annotation()

    norm_chr = chr_label(chr_name)
    if fasta_path and Path(fasta_path).is_file():
        idx = fasta_index if fasta_index is not None else load_fasta_index(fasta_path)
        rec = _lookup_contig(idx, chr_name)
        if rec is None:
            return {
                "status": "no_reference_for_chr",
                "bracketed": "",
                "raw": "",
                "fasta_ref_base": "",
                "vcf_ref_base": str(ref_allele or "").strip().upper(),
                "ref_mismatch": False,
                "warning": f"no_reference_for_chr: kromosom '{chr_name}' tidak ditemukan pada FASTA referensi.",
                "synthetic": False,
            }

        contig_len = rec["length"]
        start_1 = max(1, int(pos) - flank_len)
        end_1 = min(contig_len, int(pos) + flank_len)
        seq_slice = _read_fasta_region(Path(fasta_path), chr_name, start_1, end_1, index=idx) or ""
        center_idx = int(pos) - start_1
        if 0 <= center_idx < len(seq_slice):
            left = seq_slice[:center_idx]
            fasta_ref_base = seq_slice[center_idx]
            right = seq_slice[center_idx + 1:]
        else:
            return {
                "status": "no_reference_for_chr",
                "bracketed": "",
                "raw": "",
                "fasta_ref_base": "",
                "vcf_ref_base": str(ref_allele or "").strip().upper(),
                "ref_mismatch": False,
                "warning": f"Posisi {chr_name}:{pos} di luar panjang contig ({contig_len} bp).",
                "synthetic": False,
            }

        vcf_ref_clean = str(ref_allele or "").strip().upper()
        vcf_alt_clean = str(alt_allele or "").strip().upper()
        mid = (allele or fasta_ref_base or "N").strip().upper()[0]

        ref_mismatch = False
        warning: str | None = None
        if len(vcf_ref_clean) == 1 and vcf_ref_clean in "ACGT" and fasta_ref_base in "ACGT":
            if vcf_ref_clean != fasta_ref_base:
                ref_mismatch = True
                warning = (
                    f"ref_mismatch: basa referensi FASTA '{fasta_ref_base}' pada {norm_chr}:{pos} "
                    f"tidak cocok dengan REF VCF '{vcf_ref_clean}' (indikasi perbedaan genome build)."
                )
        elif vcf_alt_clean in {"A", "C", "G", "T"} and fasta_ref_base in "ACGT":
            # Ketika hanya allele minor/major tersedia tanpa kolom REF eksplisit
            if vcf_alt_clean != fasta_ref_base and not vcf_ref_clean:
                pass

        return {
            "status": "ok",
            "bracketed": f"{left}[{mid}]{right}",
            "raw": f"{left}{mid}{right}",
            "snp_index_0based": len(left),
            "fasta_ref_base": fasta_ref_base,
            "vcf_ref_base": vcf_ref_clean,
            "ref_mismatch": ref_mismatch,
            "warning": warning,
            "synthetic": False,
        }

    if not allow_synthetic:
        return {
            "status": "no_reference",
            "bracketed": "",
            "raw": "",
            "fasta_ref_base": "",
            "vcf_ref_base": str(ref_allele or "").strip().upper(),
            "ref_mismatch": False,
            "warning": None,
            "synthetic": False,
        }

    # Mode simulasi eksplisit (hanya bila ALLOW_SYNTHETIC_ANNOTATION=true dan FASTA tidak tersedia)
    clean_allele = (allele or "C").strip().upper()
    mid = clean_allele[0] if clean_allele and clean_allele[0] in "ACGT" else "C"
    motif = "ACGTCGTAGCATGCTAGCTA"
    left_seq = (motif * ((flank_len // len(motif)) + 1))[:flank_len]
    right_seq = (motif * ((flank_len // len(motif)) + 1))[:flank_len]
    return {
        "status": "ok",
        "bracketed": f"{left_seq}[{mid}]{right_seq}",
        "raw": f"{left_seq}{mid}{right_seq}",
        "snp_index_0based": len(left_seq),
        "fasta_ref_base": "",
        "vcf_ref_base": str(ref_allele or "").strip().upper(),
        "ref_mismatch": False,
        "warning": None,
        "synthetic": True,
    }


def extract_or_build_flanking_sequence(
    chr_name: str,
    pos: int,
    allele: str = "C",
    flank_len: int = 100,
    fasta_path: str | Path | None = None,
    allow_synthetic: bool | None = None,
) -> tuple[str, str]:
    """
    Mengekstrak sekuens flanking `flank_len` bp di kiri dan kanan SNP (`201 bp` total).
    Mengembalikan `(bracketed_sequence, raw_201bp_sequence)` atau `("", "")` jika referensi
    FASTA tidak tersedia / kromosom absen dan `ALLOW_SYNTHETIC_ANNOTATION` nonaktif.
    """
    detail = _extract_flanking_detail(
        chr_name=chr_name,
        pos=pos,
        allele=allele,
        flank_len=flank_len,
        fasta_path=fasta_path,
        allow_synthetic=allow_synthetic,
    )
    return detail["bracketed"], detail["raw"]


def design_primers_for_flanking(
    raw_seq: str,
    snp_index_0based: int = 100,
    max_pairs: int = 5,
    gc_min: float = 35.0,
    gc_opt: float = 42.5,
    gc_max: float = 55.0,
    tm_min: float = 50.0,
    tm_opt: float = 55.0,
    tm_max: float = 58.0,
    product_min: int = 100,
    product_max: int = 190,
) -> list[dict[str, Any]]:
    """Mendesain pasangan primer (digantikan engine Primer3 penuh pada A59)."""
    seq = raw_seq.upper()
    n_len = len(seq)
    if n_len < 120:
        return []

    overall_gc = calculate_gc_percent(seq)
    if overall_gc < 26.0:
        return []

    left_candidates: list[dict[str, Any]] = []
    max_left_start = min(snp_index_0based - 18, n_len - 40)
    for start_0 in range(1, max(2, max_left_start), 2):
        for length in (20, 21, 19, 22, 18):
            if start_0 + length > snp_index_0based - 2:
                continue
            oligo = seq[start_0:start_0 + length]
            if "N" in oligo or re.search(r"(A{5,}|T{5,}|G{5,}|C{5,})", oligo):
                continue
            gc = calculate_gc_percent(oligo)
            if not (gc_min <= gc <= gc_max):
                continue
            tm = calculate_tm_santalucia(oligo)
            if not (tm_min <= tm <= tm_max):
                continue
            any_th = _score_secondary_structure(oligo, None, three_prime_only=False)
            three_th = _score_secondary_structure(oligo, None, three_prime_only=True)
            hairpin = 0.0
            penalty = abs(tm - tm_opt) * 1.5 + abs(gc - gc_opt) * 0.25 + any_th * 0.05 + three_th * 0.1
            left_candidates.append({
                "start": start_0 + 1,
                "len": length,
                "tm": tm,
                "gc": gc,
                "any_th": any_th,
                "three_th": three_th,
                "hairpin": hairpin,
                "seq": oligo,
                "penalty": penalty,
            })
            break

    right_candidates: list[dict[str, Any]] = []
    min_right_5prime = max(snp_index_0based + 22, 118)
    for end_1based in range(min_right_5prime, n_len + 1, 2):
        for length in (20, 21, 19, 22, 18):
            start_0 = end_1based - length
            if start_0 <= snp_index_0based + 2:
                continue
            template_slice = seq[start_0:end_1based]
            oligo_rc = reverse_complement(template_slice)
            if "N" in oligo_rc or re.search(r"(A{5,}|T{5,}|G{5,}|C{5,})", oligo_rc):
                continue
            gc = calculate_gc_percent(oligo_rc)
            if not (gc_min <= gc <= gc_max):
                continue
            tm = calculate_tm_santalucia(oligo_rc)
            if not (tm_min <= tm <= tm_max):
                continue
            any_th = _score_secondary_structure(oligo_rc, None, three_prime_only=False)
            three_th = _score_secondary_structure(oligo_rc, None, three_prime_only=True)
            hairpin = 0.0
            penalty = abs(tm - tm_opt) * 1.5 + abs(gc - gc_opt) * 0.25 + any_th * 0.05 + three_th * 0.1
            right_candidates.append({
                "start": end_1based,
                "len": length,
                "tm": tm,
                "gc": gc,
                "any_th": any_th,
                "three_th": three_th,
                "hairpin": hairpin,
                "seq": oligo_rc,
                "penalty": penalty,
            })
            break

    if not left_candidates or not right_candidates:
        return []

    left_candidates.sort(key=lambda x: x["penalty"])
    right_candidates.sort(key=lambda x: x["penalty"])

    pairs: list[dict[str, Any]] = []
    used_left_starts: set[int] = set()
    used_right_starts: set[int] = set()

    for lc in left_candidates[:20]:
        for rc in right_candidates[:20]:
            prod_size = rc["start"] - lc["start"] + 1
            if not (product_min <= prod_size <= product_max):
                continue
            if abs(lc["tm"] - rc["tm"]) > 2.5:
                continue
            if lc["start"] in used_left_starts and rc["start"] in used_right_starts:
                continue

            pair_any = _score_secondary_structure(lc["seq"], rc["seq"], three_prime_only=False)
            pair_3 = _score_secondary_structure(lc["seq"], rc["seq"], three_prime_only=True)
            pair_penalty = lc["penalty"] + rc["penalty"] + abs(lc["tm"] - rc["tm"]) * 2.0 + pair_any * 0.05

            pairs.append({
                "left": lc,
                "right": rc,
                "product_size": prod_size,
                "pair_any_th_compl": pair_any,
                "pair_3_th_compl": pair_3,
                "pair_penalty": round(pair_penalty, 3),
            })
            used_left_starts.add(lc["start"])
            used_right_starts.add(rc["start"])
            break
        if len(pairs) >= max_pairs:
            break

    pairs.sort(key=lambda p: p["pair_penalty"])
    return pairs[:max_pairs]


def format_primer3_block(pairs: list[dict[str, Any]]) -> str:
    """Memformat daftar pasangan primer dalam format blok output standar."""
    if not pairs:
        return "no primers found"

    lines = ["OLIGO            start  len      tm     gc%  any_th  3'_th hairpin seq"]
    for idx, p in enumerate(pairs):
        if idx > 0:
            lines.append("")
        lf = p["left"]
        rg = p["right"]
        lines.append(
            f"LEFT PRIMER     {lf['start']:6d} {lf['len']:4d} {lf['tm']:7.2f} {lf['gc']:7.2f} "
            f"{lf['any_th']:7.2f} {lf['three_th']:6.2f} {lf['hairpin']:7.2f} {lf['seq']}"
        )
        lines.append(
            f"RIGHT PRIMER    {rg['start']:6d} {rg['len']:4d} {rg['tm']:7.2f} {rg['gc']:7.2f} "
            f"{rg['any_th']:7.2f} {rg['three_th']:6.2f} {rg['hairpin']:7.2f} {rg['seq']}"
        )
        lines.append(
            f"PRODUCT SIZE: {p['product_size']}, "
            f"PAIR ANY_TH COMPL: {p['pair_any_th_compl']:.2f}, "
            f"PAIR 3'_TH COMPL: {p['pair_3_th_compl']:.2f}"
        )
    return "\n".join(lines)


def generate_flanking_and_primers(
    lead_snps: list[dict[str, Any]],
    trait_name: str = "trait",
    max_snps: int = 10,
    fasta_path: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Membangkitkan sekuens flanking 201 bp dari FASTA terindeks `.fai` dan desain primer untuk Lead SNPs.
    """
    clean_trait = re.sub(r"\s+", "_", str(trait_name or "trait").strip().lower())
    selected_snps = list(lead_snps)[:max_snps]
    allow_synthetic = _allow_synthetic_annotation()

    fasta_obj = Path(fasta_path) if fasta_path and Path(fasta_path).is_file() else None
    fasta_index = load_fasta_index(fasta_obj) if fasta_obj is not None else None

    items: list[dict[str, Any]] = []
    for idx, s in enumerate(selected_snps, start=1):
        raw_chr = chr_label(s.get("chr") or "1")
        pos = int(s.get("pos", 0))
        ref_val = str(s.get("ref") or s.get("vcf_ref") or "").strip().upper()
        alt_val = str(s.get("alt") or s.get("a1") or "").strip().upper()
        allele = alt_val or ref_val or "C"
        marker_label = f"{clean_trait}_{raw_chr}_{pos}"

        detail = _extract_flanking_detail(
            chr_name=raw_chr,
            pos=pos,
            allele=allele,
            ref_allele=ref_val,
            alt_allele=alt_val,
            flank_len=100,
            fasta_path=fasta_obj,
            fasta_index=fasta_index,
            allow_synthetic=False,
        )
        if detail["status"] == "no_reference" and allow_synthetic:
            detail = _extract_flanking_detail(
                chr_name=raw_chr,
                pos=pos,
                allele=allele,
                ref_allele=ref_val,
                alt_allele=alt_val,
                flank_len=100,
                fasta_path=None,
                allow_synthetic=True,
            )

        bracketed_seq = detail["bracketed"]
        raw_201_seq = detail["raw"]
        if not raw_201_seq:
            items.append({
                "rank": idx,
                "marker_id": marker_label,
                "snp": s.get("snp") or f"chr{raw_chr}:{pos}",
                "chr": raw_chr,
                "pos": pos,
                "allele": allele,
                "p": s.get("p"),
                "nearest_gene": s.get("nearest_gene", "-"),
                "category": s.get("category", "-"),
                "flanking_sequence": "",
                "sequence_length": 0,
                "gc_percent": 0.0,
                "primer_pairs_count": 0,
                "primer_pairs": [],
                "primer3_output": "",
                "status": detail["status"],
                "ref_mismatch": False,
                "fasta_ref_base": detail.get("fasta_ref_base", ""),
                "vcf_ref_base": detail.get("vcf_ref_base", ""),
                "warning": detail.get("warning"),
                "synthetic": False,
            })
            continue

        snp_idx = int(detail.get("snp_index_0based", len(raw_201_seq) // 2))
        pairs = design_primers_for_flanking(raw_201_seq, snp_index_0based=snp_idx, max_pairs=5)
        block_text = format_primer3_block(pairs)

        items.append({
            "rank": idx,
            "marker_id": marker_label,
            "snp": s.get("snp") or f"chr{raw_chr}:{pos}",
            "chr": raw_chr,
            "pos": pos,
            "allele": allele,
            "p": s.get("p"),
            "nearest_gene": s.get("nearest_gene", "-"),
            "category": s.get("category", "-"),
            "flanking_sequence": bracketed_seq,
            "sequence_length": len(raw_201_seq),
            "gc_percent": calculate_gc_percent(raw_201_seq),
            "primer_pairs_count": len(pairs),
            "primer_pairs": pairs,
            "primer3_output": block_text,
            "status": "primers_found" if pairs else "no_primers_found",
            "ref_mismatch": bool(detail.get("ref_mismatch")),
            "fasta_ref_base": detail.get("fasta_ref_base", ""),
            "vcf_ref_base": detail.get("vcf_ref_base", ""),
            "warning": detail.get("warning"),
            "synthetic": bool(detail.get("synthetic")),
        })

    has_synthetic = any(bool(it.get("synthetic")) for it in items)
    all_no_reference = (
        (not items and fasta_obj is None and not allow_synthetic)
        or (bool(items) and all(it["status"] in {"no_reference", "no_reference_for_chr"} for it in items))
    )
    payload = {
        "status": "no_reference" if all_no_reference else "completed",
        "synthetic": has_synthetic,
        "trait": trait_name,
        "total_markers": len(items),
        "markers_with_primers": sum(1 for it in items if it["primer_pairs_count"] > 0),
        "ref_mismatch_count": sum(1 for it in items if it.get("ref_mismatch")),
        "markers": items,
    }

    if output_dir is not None:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        txt_file = out_path / "Flanking_dan_Primer3.txt"
        txt_blocks: list[str] = []
        for it in items:
            txt_blocks.append(f"### {it['rank']}. {it['marker_id']} ({it['snp']})")
            if it["status"] in {"no_reference", "no_reference_for_chr"}:
                txt_blocks.append(f"> [{it['status']}]")
                txt_blocks.append(it["status"])
            else:
                txt_blocks.append(f"> {it['flanking_sequence']}\n")
                if it.get("warning"):
                    txt_blocks.append(f"[WARNING] {it['warning']}")
                txt_blocks.append(it["primer3_output"])
            txt_blocks.append("")
        txt_file.write_text("\n".join(txt_blocks), encoding="utf-8")

        csv_file = out_path / "Desain_Primer_Kandidat.csv"
        with csv_file.open("w", encoding="utf-8", newline="") as cf:
            writer = csv.writer(cf)
            writer.writerow([
                "Marker_ID",
                "SNP",
                "Chr",
                "Pos",
                "Pair_Index",
                "Left_Start",
                "Left_Len",
                "Left_Tm",
                "Left_GC",
                "Left_Seq",
                "Right_Start",
                "Right_Len",
                "Right_Tm",
                "Right_GC",
                "Right_Seq",
                "Product_Size_bp",
                "Flanking_201bp",
            ])
            for it in items:
                if it["status"] in {"no_reference", "no_reference_for_chr"}:
                    writer.writerow([
                        it["marker_id"],
                        it["snp"],
                        it["chr"],
                        it["pos"],
                        0,
                        "-",
                        "-",
                        "-",
                        "-",
                        it["status"],
                        "-",
                        "-",
                        "-",
                        "-",
                        it["status"],
                        "-",
                        "",
                    ])
                elif not it["primer_pairs"]:
                    writer.writerow([
                        it["marker_id"],
                        it["snp"],
                        it["chr"],
                        it["pos"],
                        0,
                        "-",
                        "-",
                        "-",
                        "-",
                        "no primers found",
                        "-",
                        "-",
                        "-",
                        "-",
                        "no primers found",
                        "-",
                        it["flanking_sequence"],
                    ])
                else:
                    for p_idx, pair in enumerate(it["primer_pairs"], start=1):
                        lf = pair["left"]
                        rg = pair["right"]
                        writer.writerow([
                            it["marker_id"],
                            it["snp"],
                            it["chr"],
                            it["pos"],
                            p_idx,
                            lf["start"],
                            lf["len"],
                            lf["tm"],
                            lf["gc"],
                            lf["seq"],
                            rg["start"],
                            rg["len"],
                            rg["tm"],
                            rg["gc"],
                            rg["seq"],
                            pair["product_size"],
                            it["flanking_sequence"],
                        ])

    return payload
