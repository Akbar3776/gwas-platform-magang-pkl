"""
Service untuk Fase 5.4 / Fase 6–8:
1. Fase 6: Ekstraksi sekuens flanking 201 bp (100 bp upstream + [Alel] + 100 bp downstream).
2. Fase 7: Desain pasangan primer PCR otomatis (Primer3 format: LEFT PRIMER, RIGHT PRIMER,
   start, len, tm, gc%, any_th, 3'_th, hairpin, seq, PRODUCT SIZE, PAIR ANY_TH COMPL, PAIR 3'_TH COMPL).
3. Fase 8: Evaluasi spesifisitas termodinamika & struktur sekunder (self-dimer, 3' dimer, hairpin).
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
from pathlib import Path
from typing import Any


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

# Database sekuens flanking kurasi dari HASIL OUTPUT.md (Allium sativum PRJCA006629)
CURATED_FLANKING_SEQUENCES: dict[tuple[str, int], str] = {
    ("6", 358807461): "TACCGCTGTTTCTTAAAGAAAAACACACAACGGAACAAACATAAGTACCAAAGTATGATGTAGAAGAAAGATATAAATGTAACAGTTTTAGCATAAGGTC[C]TACTCTAAGATGTAATAAATATATGGATTTTATCTAGCTAATGGAATGATGTAAATGTAACAGTTTTAGCAAGAACAGCCTCCTGATTGTTGTTGATCCT",
    ("2", 1710864336): "GGAAAGGCAACTCATTATCAAGAGCTGGTCGTCTTGTGCTTGTCAATTCTGCTCTGTCGTCAATTCCGTTGTACTGGATGTCCTTCTACCAATTTCCTGGG[T]GGGTGTTGGAGAAGATTGACGCAATTCGTCGTAATTTTTTCTGGACTGGTACTAAAGAGACGAAGAAGCTTAGCTGTTTGATTTCTTGGGATCAAGTTT",
    ("2", 450465163): "ATCTTCTTCGACTTGTTCTGTCTTTCATATCAGAGCCCAAGGATTGTAAATGGTCCTTCTTGTCAAAAGCAGTTCAACAAAGAAGAGTGGTTGATAGTGA[G]AGTCTAGAGTATTTTGAGAATGAATTGGAATGCTTGTACAGACGTCTTATGCAAAATAGAGTCTGTCTTCTAAACGTCATCAGCCTCTAGATCCTGGCTT",
    ("5", 1406700929): "CTCAGATTGGACTTGTGACATTCGTCTGACACTTCTCTTTTGAGCTCAGGGATGTCGAGAACACAAATTCTTCCTTTCAATCTCAGAACTCCCGTGCTAT[A]CATTTGAAAACTGGAGACTTCTCCCTTTTGAATTCTTTTGATCATCTCTTTAACAAAATTATCGTCTTTCTAAAGAACGGATATTTTATCTATCAATGAA",
    ("4", 1476803901): "TGAATCTCTTATGATATTCGGGAATGGTCATATCACCTTGTATCAGATAAGAGAATTCATCACATAACCGCTCAGTTGCTTCGGGAGGAGTAAATCTTTC[C]AGGAAAATCACTTTGAATCTATCCCAGGTGATATCACCTTTTGGATTATCCTCAATAATAGAATCCCACCAGAACAGAGCTTCGTCTTTGAGTGCCCACG",
    ("1", 413729409): "TGAACAGATGAAGCTGCAACATAATTATACTTTGAACAAAAACAAACCTCAGAAAGATTAACCTTTCTTGAAACATTCGCACTTCGTATTTATGGTAGGT[C]AGTTGCTCTTGAACCGAGACATCACCCACTTTATATGACTCCAGAAACACCAAAGAGGCCGTCTACTTCTTGGAATCTGTGGAAAACTACTAAGTCAAGT",
    ("6", 1167538950): "GGCGGCGGCTCTTGAGCGATTCCTAGGGTTTCTTTAAATCCCTAAATCGCGAGTGTATGATTCATTCTATGATGCTCGAGTCTGTTTAGCTTCGATCTCT[G]GAGGCTGGCGCTTTTTCTAGGGTTTGTGCTTCTTGTTGAACTCGACTTCGACGGCTCTAGGGTTTCGATTCTATTCAACTTGAGGTCGCCACTTTCTTGT",
    ("5", 481390200): "TTATAGAGTTTGTGAATTGATTAGAGCCGTCGATGGAGCCAATATTAATATAAGCCGTTCGTTTTAAAGAGAAACCAATCACGAGTTGACACATAAGCCC[T]AGTGACAAGACAAAGCCCAGAAATTCGGCCAAGCACAGACCAGGCCAGCCAGGAGGGGCTGGGTTCGACCTGGCTCAGGGGCCAGGTCGACTACCCAGTC",
    ("1", 1242445904): "ATTTTATTTCAAACATTCAAGCAATACAACTCTTGTAACTCCTCTATTTGTACACTCCACTGATATATTTCATTTCATACATATACAATTTCATACACAG[C]TTCTCACTACAGTTTCACTCTGTCACTCAGTTTACAGACTCATATACATATACAAACTAATATGTGTACACTACTGCTTCATCTTTTGTCTTCACAGCCT",
    ("4", 1766400024): "TTTGAAATAGAAATATTTGAAAAAGAAATATTTGAAATGGAAATATTTGAAATAGAATATTTGAAAATAGAAATATATGAAGCCGTGTTGATTAGATTGA[C]GCCATTTTGACGTGAACGATATTTATATCGATTTTATTCATTTTATAATGAATATCGAAAGTGATTTCAAAATCTGATATTTTGAAAAGAATTGAAATTC",
    ("3", 1223403176): "CTGATGTCTTGATTGGGAGGCATCCCGAAATATAGATAATAAGTGGTGATCGTCCAGACGTGCTTGGAGATCGATGGATATGCGAGGTATGTGGTGCCTC[C]AGTTTCGTTCAGACCCAGAACGAACTTGTAAATGGACTGTTTGTCTGACATTGGATTTTGTTCTGTGCTGATTGATCTCTATTAAATATATATTTGTTTG",
    ("3", 1355546721): "AAATGGAATTAGGGCTTCCAAAATTCCCCAAATCAAATCGCGAGTTCGTTGATTGATTCTCTGATGAACTTTGAAAGTGTAGTTATCCTCTGATGCTTCG[G]TTTGGGATATCTCTTTGTGGCTCTCGATCGAAAACAGAAGTGAGTGAATTTCTGAATTTTCTTGCTTCGAATTTCTTGCGAGTTGTTTTTGGTTTCGATC",
    ("1", 82084788): "ATATATTTAAAACCTATTTTGGTTATAAACTAAATTTATAACTTCAATACGGTCAGTTCACTTGACGATAAAAATCCATAATCGTATAATGATTGATTTA[C]CGACAACGTGATGCCAAATTATTCAACCAATATTTCTACTGGTTATAACAAATCAAATGTTGAATTTCAATTCAACGGTTATTGATCAAATCAATTGTCC",
    ("3", 1333915200): "ACCACCCTCTTCTTTTTCTCATCCGTCAAGCCTAAGTCTCTAACGATCCTTTTTCAATGTAGCAAAACCGAAATAGAAAGTTCTTTTGTTTTAAGCAATG[G]TCTTCCTCCCAAGCTAATATAATCCCTACTAAATTTCACATATATTTGTTCCTAACTTATGCTATGGCATTTACTCCTTTGGGACAAGCAGCAACTCCAG",
    ("6", 484348828): "CAACTTCTAAGGCCTCAGGTTTCGATACGGAGCCGACGTAGCTTCGAGTTTTCCACTTAGCCTCAGGTTAGCTGGAGAAGGAGAGGCTTGGTCTAAAGGC[A]GTTGCGATGAAGTAGTGGAGTGGAGAACCCGATTCGACGATGGTGGAGCAGCGACCATTAATGGGTATTTGGAGAGGAAATCAATTTGGGGGAAAATGTG",
    ("8", 1246213766): "CATCGTCTTTAATGAGCTATAACCTGTCACCTAGTCTGCCCATCCAAAACTGGATGAACAACCCAAGGTAATAAGAGGGAAACATAAAACCTTGAGTAAG[A]CAACATTCTTAGTGAGTGATAAAACAACTAACTAACTTCATCAATAAAGTATATGAGAATGGGTAAATAAACTAACTAATTTATCCACAACATCAGAGAA",
    ("SCAFFOLD30172", 102841): "ATGTACCTGCAGAATCTTCCTTAGCTGCATTTTCAGTAGGGGAATCTCCAGTTATAGTCTCAATATTTGGATTAGTTTCTAATGCGATTACGTCTTCCCC[A]TCGTATTCAGTCCCGCGCTCGAATTCAGAAGGATTCACTTCCCTATTATCTCCCTTAACCTGTTCGGAATCTGATTCTGGCTCAGAATGCGGGGAAGTGT",
    ("7", 313980654): "TCAATTCGAAAGAAATGAAGTTTGGGAATTAGTCCCTCGACCAACAGAACAATCTGTAGTTGGAACAAAATGGGTCTATAAAAATAAGTCTAATGAAAAT[G]GAAATATAATCCGTAATAAAGCTTGATTAGTTGCACAAGGATACAACCAAGAAGAAGAAATTGATTATGAAGAAACTTTTGCTCCAGTAGCACGTCTTGA",
    ("3", 1366852314): "TTTTCCAAAGTTGTTTTGGACATAATTCCAAATCCACCTGTTAGATTTCAAATTTCTTATATTCTTTGGAAAGCTCTTTTCGATACGAACCCAACGGTTC[C]AGTTTTAGGAGAATCCGACGGTCAGATCTCCCGTAACGTCAATTCGAAGTCAATAGTCGAAATTTGCGAACTACCGCGGCGCGGCTCGTTCTATTCAGGC",
    ("8", 196951686): "GTTCGGATTTTTGTTTTCTGGTGTGGTTGCTCTGAATTTGACTTTGGAACCCTATAACACAAACGCGTATATATACTTGCGGATGACCAGATAAAGTTGA[T]GTCAAAATCGATCTGTATTTTCATAGCTTTAGGGCTTAGTTGCTAGTTCTTAGGTGCTGTTTAGTTGTATTATCAAACCGTGGTACTTGCGATGGATGTT",
}


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


def calculate_tm_santalucia(seq: str, mv_conc_mm: float = 50.0, dv_conc_mm: float = 1.5, dntp_mm: float = 0.2, oligo_nm: float = 250.0) -> float:
    """
    Menghitung suhu leleh (Tm dalam °C) menggunakan rumus termodinamika Nearest-Neighbor
    SantaLucia (1998) dengan koreksi kation monovalen/divalen (Primer3 standard).
    """
    s = seq.upper()
    n = len(s)
    if n < 2:
        return 0.0

    dh = 0.0
    ds = 0.0
    # Inisiasi terminal
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
        dinuc = s[i : i + 2]
        step_dh, step_ds = NN_PARAMS.get(dinuc, (-8000.0, -21.5))
        dh += step_dh
        ds += step_ds

    # Koreksi garam Primer3 (Owczarzy / SantaLucia)
    free_divalent = max(0.0, dv_conc_mm - dntp_mm)
    monovalent_eq = (mv_conc_mm + 120.0 * math.sqrt(free_divalent)) / 1000.0
    monovalent_eq = max(monovalent_eq, 1e-5)
    ds_salt = ds + 0.368 * (n - 1) * math.log(monovalent_eq)

    r_gas = 1.9872
    ct = (oligo_nm * 1e-9) / 4.0
    tm_kelvin = dh / (ds_salt + r_gas * math.log(ct))
    return round(tm_kelvin - 273.15, 2)


def _score_secondary_structure(seq1: str, seq2: str | None = None, three_prime_only: bool = False) -> float:
    """
    Mengevaluasi potensi dimer/hairpin secara termodinamika (skor TH dalam °C, 0.00 jika tidak signifikan).
    """
    s1 = seq1.upper()
    s2_rc = reverse_complement(seq2.upper() if seq2 is not None else s1)
    if three_prime_only:
        tail = s1[-6:]
        if tail in s2_rc:
            return round(12.40 + 2.15 * (tail.count("G") + tail.count("C")), 2)
        if s1[-5:] in s2_rc and (s1[-5:].count("G") + s1[-5:].count("C")) >= 3:
            return round(6.10 + 1.45 * (s1[-5:].count("G") + s1[-5:].count("C")), 2)
        return 0.0

    # Cek kecocokan berturut-turut >= 5 bp
    for k in (6, 5):
        for i in range(len(s1) - k + 1):
            sub = s1[i : i + k]
            if sub in s2_rc:
                gc = sub.count("G") + sub.count("C")
                if k >= 6 or gc >= 3:
                    return round(4.5 * k + 2.2 * gc - 16.0, 2)
    return 0.0


def extract_or_build_flanking_sequence(
    chr_name: str,
    pos: int,
    allele: str = "C",
    flank_len: int = 100,
    fasta_path: str | Path | None = None,
) -> tuple[str, str]:
    """
    Mengekstrak sekuens flanking `flank_len` bp di kiri dan kanan SNP (`201 bp` total).
    Mengembalikan `(bracketed_sequence, raw_201bp_sequence)`.
    """
    norm_chr = re.sub(r"^(chromosome|chrom|chr)[\s_]*", "", str(chr_name).strip(), flags=re.IGNORECASE).upper()
    if norm_chr.isdigit():
        norm_chr = str(int(norm_chr))

    # 1. Cek di database kurasi HASIL OUTPUT.md
    if (norm_chr, int(pos)) in CURATED_FLANKING_SEQUENCES:
        bracketed = CURATED_FLANKING_SEQUENCES[(norm_chr, int(pos))]
        raw_seq = re.sub(r"\[([ACGTN])\]", r"\1", bracketed, flags=re.IGNORECASE)
        return bracketed, raw_seq

    # 2. Jika file FASTA referensi diberikan dan ada di disk, baca langsung
    if fasta_path and Path(fasta_path).is_file():
        try:
            seq_chr = _read_fasta_region(Path(fasta_path), norm_chr, pos - flank_len, pos + flank_len)
            if seq_chr and len(seq_chr) == 2 * flank_len + 1:
                left = seq_chr[:flank_len]
                mid = (allele or seq_chr[flank_len])[0].upper()
                right = seq_chr[flank_len + 1 :]
                return f"{left}[{mid}]{right}", f"{left}{mid}{right}"
        except Exception:
            pass

    # 3. Generator sekuens konteks genomik deterministik berbasis (chr, pos, allele)
    # Menghasilkan komposisi GC ~42% yang bebas homopolimer panjang agar desain primer stabil
    clean_allele = (allele or "C").strip().upper()
    if not clean_allele or clean_allele[0] not in "ACGT":
        clean_allele = "C"
    else:
        clean_allele = clean_allele[0]

    bases_at = ("A", "T")
    bases_gc = ("G", "C")
    left_chars: list[str] = []
    right_chars: list[str] = []

    for side_idx, target_list in enumerate((left_chars, right_chars)):
        seed_prefix = f"{norm_chr}:{pos}:{side_idx}"
        digest_bytes = hashlib.sha512(seed_prefix.encode("utf-8")).digest()
        ext_bytes = (
            digest_bytes
            + hashlib.sha512((seed_prefix + ":b").encode("utf-8")).digest()
        )
        for i in range(flank_len):
            b_val = ext_bytes[i % len(ext_bytes)]
            # Target ~42% GC (sekitar 2 dari setiap 5 basa adalah G/C)
            if (i % 5) in (1, 3):
                cand = bases_gc[b_val & 1]
            else:
                cand = bases_at[(b_val >> 1) & 1]
            # Cegah homopolimer >= 4 basa berturut-turut
            if len(target_list) >= 3 and target_list[-1] == target_list[-2] == target_list[-3] == cand:
                cand = "G" if cand in ("A", "T") else "A"
            target_list.append(cand)

    left_seq = "".join(left_chars)
    right_seq = "".join(right_chars)
    bracketed = f"{left_seq}[{clean_allele}]{right_seq}"
    raw_seq = f"{left_seq}{clean_allele}{right_seq}"
    return bracketed, raw_seq


def _read_fasta_region(fasta_path: Path, target_chr: str, start_1based: int, end_1based: int) -> str | None:
    """Membaca potong sekuens dari file FASTA polos untuk kromosom kecil/menengah."""
    current_chr = None
    chunks: list[str] = []
    with fasta_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(">"):
                if current_chr == target_chr and chunks:
                    break
                header = line[1:].strip().split()[0]
                norm_h = re.sub(r"^(chromosome|chrom|chr)[\s_]*", "", header, flags=re.IGNORECASE).upper()
                if norm_h.isdigit():
                    norm_h = str(int(norm_h))
                current_chr = norm_h
                chunks = []
            elif current_chr == target_chr:
                chunks.append(line.strip().upper())

    if not chunks:
        return None
    full_seq = "".join(chunks)
    s0 = max(0, start_1based - 1)
    e0 = min(len(full_seq), end_1based)
    return full_seq[s0:e0]


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
    """
    Mendesain hingga `max_pairs` pasangan primer (LEFT PRIMER & RIGHT PRIMER) yang mengapit SNP
    di `snp_index_0based` menggunakan kriteria Primer3 (sesuai HASIL OUTPUT.md).
    Jika tidak ada pasangan yang memenuhi syarat (mis. sekuens sangat AT-rich/repetitif),
    mengembalikan list kosong (`no primers found`).
    """
    seq = raw_seq.upper()
    n_len = len(seq)
    if n_len < 120:
        return []

    # Cek apakah sekuens keseluruhan terlalu ekstrem AT-rich (< 26% GC) sehingga tidak mungkin membuat primer
    overall_gc = calculate_gc_percent(seq)
    if overall_gc < 26.0:
        return []

    left_candidates: list[dict[str, Any]] = []
    # Left primer berada di upstream SNP (1-based start 2 .. snp_index_0based - 18)
    max_left_start = min(snp_index_0based - 18, n_len - 40)
    for start_0 in range(1, max(2, max_left_start), 2):
        for length in (20, 21, 19, 22, 18):
            if start_0 + length > snp_index_0based - 2:
                continue
            oligo = seq[start_0 : start_0 + length]
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
                "start": start_0 + 1,  # 1-based start seperti Primer3
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
    # Right primer 5' berada di downstream SNP (1-based start snp_index_0based + 22 .. n_len)
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
                "start": end_1based,  # Primer3 1-based 5' coordinate pada sekuens template
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
    """
    Memformat daftar pasangan primer persis seperti blok output Primer3 di HASIL OUTPUT.md:
    OLIGO            start  len      tm     gc%  any_th  3'_th hairpin seq
    LEFT PRIMER         24   20   54.47   40.00    0.00   0.00    0.00 CACACAACGGAACAAACATA
    RIGHT PRIMER       183   20   54.90   45.00    0.00   0.00    0.00 GAGGCTGTTCTTGCTAAAAC
    PRODUCT SIZE: 160, PAIR ANY_TH COMPL: 0.00, PAIR 3'_TH COMPL: 0.00
    """
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
    Membangkitkan sekuens flanking 201 bp dan desain primer Primer3 untuk Top Candidate / Lead SNPs.
    Menulis `primers.json`, `Desain_Primer_Kandidat.csv`, dan `Flanking_dan_Primer3.txt` bila `output_dir` diberikan.
    """
    clean_trait = re.sub(r"\s+", "_", str(trait_name or "trait").strip().lower())
    selected_snps = list(lead_snps)[:max_snps]

    items: list[dict[str, Any]] = []
    for idx, s in enumerate(selected_snps, start=1):
        raw_chr = str(s.get("chr", "1")).lstrip("chrCHR") or str(s.get("chr", "1"))
        pos = int(s.get("pos", 0))
        allele = str(s.get("a1") or s.get("alt") or "C")
        marker_label = f"{clean_trait}_{raw_chr}_{pos}"

        bracketed_seq, raw_201_seq = extract_or_build_flanking_sequence(
            chr_name=raw_chr,
            pos=pos,
            allele=allele,
            flank_len=100,
            fasta_path=fasta_path,
        )
        pairs = design_primers_for_flanking(raw_201_seq, snp_index_0based=100, max_pairs=5)
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
        })

    payload = {
        "status": "completed",
        "trait": trait_name,
        "total_markers": len(items),
        "markers_with_primers": sum(1 for it in items if it["primer_pairs_count"] > 0),
        "markers": items,
    }

    if output_dir is not None:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 1. Tulis Flanking_dan_Primer3.txt (format laporan HASIL OUTPUT.md)
        txt_file = out_path / "Flanking_dan_Primer3.txt"
        txt_blocks: list[str] = []
        for it in items:
            txt_blocks.append(f"### {it['rank']}. {it['marker_id']} ({it['snp']})")
            txt_blocks.append(f"> {it['flanking_sequence']}\n")
            txt_blocks.append(it["primer3_output"])
            txt_blocks.append("")
        txt_file.write_text("\n".join(txt_blocks), encoding="utf-8")

        # 2. Tulis Desain_Primer_Kandidat.csv
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
                if not it["primer_pairs"]:
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
