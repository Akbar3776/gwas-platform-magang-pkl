"""
Pure-Python port of EasyKASP VBA KASP primer design (DOI: 10.1186/s12859-025-06322-x).

Note (Q16): Internal application port based on KASP_Primer/Code_in_Excel_VBA.txt.
Verify upstream EasyKASP repository license terms prior to public distribution.
"""

from __future__ import annotations

import re
from typing import Any

FAM_TAIL = "GAAGGTGACCAAGTTCATGCT"
HEX_TAIL = "GAAGGTCGGAGTCAACGGATT"

_CLEAN_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_BASE_COMP = {"A": "T", "T": "A", "C": "G", "G": "C"}


def _vba_mid(text: str, start_1based: int, length: int) -> str:
    """1-based substring slice matching Excel/VBA Mid(text, start, length)."""
    if not text or length <= 0:
        return ""
    if start_1based < 1:
        length += start_1based - 1
        if length <= 0:
            return ""
        start_1based = 1
    idx0 = start_1based - 1
    return text[idx0:idx0 + length]


def _vba_right(text: str, length: int) -> str:
    """Trailing substring slice matching Excel/VBA Right(text, length)."""
    if not text or length <= 0:
        return ""
    return text[-length:]


def _vba_left(text: str, length: int) -> str:
    """Leading substring slice matching Excel/VBA Left(text, length)."""
    if not text or length <= 0:
        return ""
    return text[:length]


def _vba_instr(text: str, sub: str) -> int:
    """1-based substring search matching Excel/VBA InStr(1, text, sub)."""
    idx = text.find(sub)
    return 0 if idx < 0 else idx + 1


def _revcomp_acgt_only(seq: str) -> str:
    """Reverse-complement strict A/T/C/G characters (ignoring Q padding/non-ACGT as in VBA)."""
    return "".join(_BASE_COMP[ch] for ch in reversed(seq.upper()) if ch in _BASE_COMP)


def _count_bases(seq: str) -> tuple[int, int, int, int]:
    """Return (ka, kt, kc, kg) counts on uppercase sequence."""
    up = seq.upper()
    return up.count("A"), up.count("T"), up.count("C"), up.count("G")


def _has_homopolymer(seq: str, min_run: int = 6) -> bool:
    """Check whether sequence contains A/T/C/G homopolymer of length min_run."""
    up = seq.upper()
    return any((b * min_run) in up for b in ("A", "T", "C", "G"))


def _easykasp_tm(ka: int, kt: int, kc: int, kg: int, denom_offset: int = 0) -> float:
    """Compute EasyKASP empirical Tm formula."""
    total = ka + kt + kc + kg + denom_offset
    if total <= 0:
        return 0.0
    return 72.0 + (37.4 * (kg + kc) - 747.0) / total


def _score_allele_window(seq: str, tm_min: float = 56.6, tm_max: float = 59.0) -> float | None:
    """Evaluate EasyKASP Tm and GC/homopolymer gates for allele-specific candidates."""
    ka, kt, kc, kg = _count_bases(seq)
    n_acgt = ka + kt + kc + kg
    if n_acgt < 18 or len(seq) != n_acgt:
        return None
    gc_frac = (kg + kc) / n_acgt
    if gc_frac < 0.16 or gc_frac > 0.72:
        return None
    tm = _easykasp_tm(ka, kt, kc, kg)
    if tm < tm_min or tm > tm_max:
        return None
    if _has_homopolymer(seq, 6):
        return None
    return tm


def _score_common_window(seq: str) -> float | None:
    """Evaluate EasyKASP Tm, GC, and homopolymer gates for common primer candidates."""
    ka, kt, kc, kg = _count_bases(seq)
    n_acgt = ka + kt + kc + kg
    if n_acgt < 20 or len(seq) != n_acgt:
        return None
    gc_frac = (kg + kc) / n_acgt
    if gc_frac < 0.21 or gc_frac > 0.70:
        return None
    tm = _easykasp_tm(ka, kt, kc, kg)
    if tm < 59.9 or tm > 62.9:
        return None
    if abs(tm - 60.4) > 0.57:
        return None
    if _has_homopolymer(seq, 6):
        return None
    left4 = _vba_left(seq, 4)
    right4 = _vba_right(seq, 4)
    if left4 in {"AAAA", "TTTT", "CCCC", "GGGG"} or right4 in {"AAAA", "TTTT", "CCCC", "GGGG"}:
        return None
    return tm


def _select_allele_candidate(
    ss: str,
    take_fn: Any,
    initial_k: int,
    c8_upper_exit: float,
    early_tm_limit: float,
) -> tuple[str, float | None]:
    """Sweep k downward cleanly matching VBA loops for c5 (38..18) and c6 (m2+len..18)."""
    k = initial_k
    c_tm: float | None = None
    cand = ""
    while c_tm is None or c_tm >= 56.6:
        if c_tm is not None and c_tm >= 56.6:
            k -= 1
        cand = take_fn(ss, k)
        n = len(cand)
        if n < 18 and c_tm is not None and 0 < c_tm <= 59.0:
            cand = take_fn(ss, k + 1)
        if k < 18:
            break
        ka, kt, kc, kg = _count_bases(cand)
        n_acgt = ka + kt + kc + kg
        if n_acgt < 18:
            break

        j = 0.0
        if len(cand) == n_acgt:
            gc_frac = (kg + kc) / n_acgt
            if 0.16 <= gc_frac <= 0.72:
                j = _easykasp_tm(ka, kt, kc, kg)
        if 56.6 <= j <= 59.0:
            c_tm = j

        if c_tm is not None and _has_homopolymer(cand, 6):
            c_tm = None
        if c_tm is not None and 0 < c_tm <= early_tm_limit:
            break
        if c_tm is not None and c_tm > 0:
            if len(cand) == n_acgt and _easykasp_tm(ka, kt, kc, kg, denom_offset=-1) < 56.1:
                cand = take_fn(ss, k + 1)
                break
        if c_tm is None:
            k -= 1

    if c_tm is not None and 0 < c_tm <= c8_upper_exit:
        return cand, c_tm
    return "", c_tm


def _pick_common_primer_from_window(
    flank: str,
    kk1: int,
    kk3: int,
    upstream: bool,
) -> str:
    """Sweep offset jj=4..30 to find optimal common primer matching EasyKASP VBA rules."""
    chosen = ""
    jj = 4
    while jj <= 30:
        k = 40
        if upstream and kk1 < k + jj + 18:
            break
        raw_window = _vba_mid(flank, kk1 - k - jj, k) if upstream else _vba_mid(flank, kk3 + jj, k)
        c10: float | None = None
        cand = ""
        while c10 is None or c10 >= 59.9:
            if c10 is not None and c10 >= 59.9:
                k -= 1
            cand = _vba_right(raw_window, k) if upstream else _vba_left(raw_window, k)
            n = len(cand)
            if n < 20 and c10 is not None and c10 >= 59.9:
                cand = (
                    _vba_mid(flank, kk1 - k - 1 - jj, k + 1)
                    if upstream
                    else _vba_mid(flank, kk3 + jj, k + 1)
                )
            if n < 20:
                break
            ka, kt, kc, kg = _count_bases(cand)
            n_acgt = ka + kt + kc + kg
            if n_acgt < 20:
                break

            j = 0.0
            if len(cand) == n_acgt:
                gc_frac = (kg + kc) / n_acgt
                if 0.21 <= gc_frac <= 0.70:
                    j = _easykasp_tm(ka, kt, kc, kg)
            if 59.9 <= j <= 62.9:
                c10 = j

            if c10 is not None and abs(c10 - 60.4) <= 0.57:
                if (
                    _has_homopolymer(cand, 6)
                    or cand[:4] in {"AAAA", "TTTT", "CCCC", "GGGG"}
                    or cand[-4:] in {"AAAA", "TTTT", "CCCC", "GGGG"}
                ):
                    c10 = None

            if c10 is not None and c10 >= 59.9:
                if len(cand) == n_acgt and _easykasp_tm(ka, kt, kc, kg, denom_offset=-1) < 59.34:
                    cand = (
                        _vba_mid(flank, kk1 - k - 1 - jj, k + 1)
                        if upstream
                        else _vba_mid(flank, kk3 + jj, k + 1)
                    )
                    break
            if c10 is None:
                k -= 1

        if c10 is not None and abs(c10 - 60.4) <= 0.57:
            if not chosen:
                chosen = cand
            edge_base = _vba_right(cand, 1) if upstream else _vba_left(cand, 1)
            chosen_edge = _vba_right(chosen, 1) if upstream else _vba_left(chosen, 1)
            if edge_base not in {"A", "T"} and abs(len(cand) - 29.5) <= 4.5:
                chosen = cand
            if chosen_edge not in {"G", "C"} and abs(len(cand) - 29.5) <= 4.5:
                chosen = cand
            if chosen != cand and abs(len(chosen) - 29) > 5 and abs(len(cand) - 29) <= abs(len(chosen) - 29):
                chosen = cand

        jj += 1
        if chosen:
            chosen_edge = _vba_right(chosen, 1) if upstream else _vba_left(chosen, 1)
            if jj >= 10 and chosen_edge not in {"A", "T"}:
                break
            if jj >= 20:
                break

    return chosen


def _build_allele_template_forward(flank: str, kk1: int, kk3: int, allele: str, h: int, kkf: int) -> str:
    ss = _vba_mid(flank, kk1 - 40, 40) + allele
    if h == 999 and len(allele) <= 38 and len(allele) >= kkf:
        ss = _vba_mid(flank, kk1 - 40, 40) + _vba_left(allele, kkf)
    elif h == 999 and len(allele) < kkf:
        ss = _vba_mid(flank, kk1 - 40, 40) + allele + _vba_mid(flank, kk3 + 1, kkf - len(allele))
    return ss


def _build_allele_template_reverse(flank: str, kk1: int, kk3: int, allele: str, h: int, kkr: int) -> str:
    ss = allele + _vba_mid(flank, kk3 + 1, 40)
    if h == 999 and len(allele) <= 38 and len(allele) >= kkr:
        ss = _vba_right(allele, kkr) + _vba_mid(flank, kk3 + 1, 40)
    elif h == 999 and len(allele) < kkr:
        ss = _vba_mid(flank, kk1 - (kkr - len(allele)), kkr - len(allele)) + allele + _vba_mid(flank, kk3 + 1, 40)
    return ss


def _design_forward(
    flank: str,
    kk1: int,
    kk3: int,
    a1: str,
    a2: str,
    m2: int,
    h: int,
    kkf: int,
    trim_c6_tttt: bool,
) -> tuple[str, str, str]:
    """Run forward-direction allele/common design (VBA FR in {0, 1} and FR = 3)."""
    ss_x = _build_allele_template_forward(flank, kk1, kk3, a1, h, kkf)
    c5, _ = _select_allele_candidate(ss_x, _vba_right, 38, 59.0, 57.8)
    if not c5:
        return "Failed", "", ""

    k_y = len(c5) + m2 if (m2 == 0 or m2 == 3) else 38
    ss_y = _build_allele_template_forward(flank, kk1, kk3, a2, h, kkf)
    c6, _ = _select_allele_candidate(ss_y, _vba_right, k_y, 59.0, 63.3)
    if c6 and trim_c6_tttt and _vba_left(c6, 4) == "TTTT":
        c6 = _vba_mid(c6, 2, 38)
    if not c6:
        return "Failed", "", ""

    chosen_c = _pick_common_primer_from_window(flank, kk1, kk3, upstream=False)
    if not chosen_c:
        return "Failed", "", ""
    return c5, c6, _revcomp_acgt_only(chosen_c)


def _design_reverse(
    flank: str,
    kk1: int,
    kk3: int,
    a1: str,
    a2: str,
    m2: int,
    h: int,
    kkr: int,
) -> tuple[str, str, str]:
    """Run reverse-direction allele/common design (VBA FR = -1)."""
    ss_x = _build_allele_template_reverse(flank, kk1, kk3, a1, h, kkr)
    raw_x, _ = _select_allele_candidate(ss_x, _vba_left, 38, 59.0, 57.8)
    if not raw_x:
        return "Failed", "", ""
    c5 = _revcomp_acgt_only(raw_x)

    k_y = len(c5) + m2 if (m2 == 0 or m2 == 3) else 38
    ss_y = _build_allele_template_reverse(flank, kk1, kk3, a2, h, kkr)
    raw_y, _ = _select_allele_candidate(ss_y, _vba_left, k_y, 59.0, 63.3)
    if not raw_y:
        return "Failed", "", ""
    c6 = _revcomp_acgt_only(raw_y)

    c7 = _pick_common_primer_from_window(flank, kk1, kk3, upstream=True)
    if not c7:
        return "Failed", "", ""
    return c5, c6, c7


def _primer_tm(seq: str) -> float:
    ka, kt, kc, kg = _count_bases(seq)
    if len(seq) == 0 or len(seq) != (ka + kt + kc + kg):
        return 0.0
    return round(_easykasp_tm(ka, kt, kc, kg), 2)


def _failed_result(snp_id: str, a1: str, a2: str, notes: list[str]) -> dict[str, Any]:
    return {
        "snp_id": str(snp_id),
        "allele_x": a1,
        "allele_y": a2,
        "primer_x": "",
        "primer_y": "",
        "primer_c": "",
        "primer_x_no_tail": "",
        "primer_y_no_tail": "",
        "direction": None,
        "tm_x": None,
        "tm_y": None,
        "tm_c": None,
        "status": "Failed",
        "notes": notes,
    }


def _choose_initial_direction(flank: str, kk1: int, kk3: int, a1: str, a2: str) -> tuple[int, int, int, int, int, bool]:
    """Replicate EasyKASP lines 64–173 direction and indel conflict analysis."""
    fr = 0
    s_f = (_vba_mid(flank, kk1 - 26, 26) + a1) if kk1 >= 27 else (_vba_mid(flank, 1, kk1 - 1) + a1)
    s_r = a1 + _vba_mid(flank, kk3 + 1, 26)
    s_f = _vba_right(s_f, 26)
    s_r = _vba_left(s_r, 26)

    ka_f, kt_f, kc_f, kg_f = _count_bases(s_f)
    if _has_homopolymer(s_f, 6) or len(s_f) != (ka_f + kt_f + kc_f + kg_f):
        fr = -1
    m_fwd = ((kg_f + kc_f) / max(kg_f + kc_f + kt_f + ka_f, 1)) * 100.0

    ka_r, kt_r, kc_r, kg_r = _count_bases(s_r)
    if _has_homopolymer(s_r, 6) or len(s_r) != (ka_r + kt_r + kc_r + kg_r):
        fr += 100
    if fr == 99:
        return fr, 0, 0, 0, 0, True
    if fr == 100:
        fr = 1

    kkf = 0
    kkr = 0
    h = 0
    if len(a1) != 1 or len(a2) != 1:
        kkf = 1
        kkr = 1
        h = 999
        long_a, short_a = (a1, a2) if len(a1) >= len(a2) else (a2, a1)
        ind_sf_l = _vba_left(long_a, 6)
        ind_sr_l = short_a + _vba_mid(flank, kk3 + 1, 6)
        while kkf < 6 and _vba_left(ind_sf_l, kkf) == _vba_left(ind_sr_l, kkf):
            kkf += 1
        ind_sf_r = _vba_right(long_a, 6)
        ind_sr_r = _vba_mid(flank, kk1 - 6, 6) + short_a
        while kkr < 6 and _vba_right(ind_sf_r, kkr) == _vba_right(ind_sr_r, kkr):
            kkr += 1
        min_limit = 5 if len(a1) >= len(a2) else 6
        if min(kkf, kkr) == min_limit or (fr == 1 and kkf == 5) or (fr == -1 and kkr == 5):
            return fr, 0, h, kkf, kkr, True
        if kkf < kkr and fr == 0:
            fr = 1
        elif kkf > kkr and fr == 0:
            fr = -1

    if a1 == a2:
        return fr, 0, h, kkf, kkr, True

    m_rev = ((kg_r + kc_r) / max(kg_r + kc_r + kt_r + ka_r, 1)) * 100.0
    gc_mode = 0
    if fr == 0:
        if abs(m_fwd - 45.0) <= abs(m_rev - 45.0):
            gc_mode = 1
            fr = 1
        else:
            gc_mode = 2
            fr = -1

    if gc_mode in {1, 2}:
        up3 = _vba_mid(flank, kk1 - 3, 3)
        up4 = _vba_mid(flank, kk1 - 3, 4)
        dn3 = _vba_mid(flank, kk3 + 1, 3)
        dn4 = _vba_mid(flank, kk3 + 1, 4)
        if "AAA" in up3 or "TTT" in up3:
            fr = -1
        if "AAAA" in dn4 or "TTTT" in dn4:
            fr = 1
        if "AAA" in dn3 or "TTT" in dn3:
            fr = 1
        if "AAAA" in up4 or "TTTT" in up4:
            fr = -1

    return fr, gc_mode, h, kkf, kkr, False


def design_kasp(
    snp_id: str,
    allele_1: str,
    allele_2: str,
    flanking: str,
) -> dict[str, Any]:
    """Design FAM/HEX allele-specific and common KASP primers using EasyKASP logic."""
    notes: list[str] = []
    a1 = str(allele_1 or "").strip().upper()
    a2 = str(allele_2 or "").strip().upper()

    flank = _CLEAN_CTRL_RE.sub("", str(flanking or "").strip()).replace(" ", "").upper()
    flank = flank.replace("-", "").replace("_", "")

    if flank.count("[") > 1:
        notes.append("Variation mistake")
        return _failed_result(snp_id, a1, a2, notes)

    if "[" not in flank or "]" not in flank or "/" not in flank:
        return _failed_result(snp_id, a1, a2, notes)
    if "," in flank or "\\" in flank or "*" in flank:
        return _failed_result(snp_id, a1, a2, notes)

    kk1 = _vba_instr(flank, "[")
    after_open = _vba_mid(flank, kk1 + 1, len(flank) - kk1)
    kk2_rel = _vba_instr(after_open, "/")
    kk3 = _vba_instr(flank, "]")
    if kk1 <= 0 or kk2_rel <= 0 or kk3 <= kk1:
        return _failed_result(snp_id, a1, a2, notes)
    kk2 = kk2_rel + kk1
    if kk2 >= kk3:
        return _failed_result(snp_id, a1, a2, notes)

    bracket_a1 = _vba_mid(flank, kk1 + 1, kk2 - kk1 - 1)
    bracket_a2 = _vba_mid(flank, kk2 + 1, kk3 - kk2 - 1)
    if a1 != bracket_a1 or a2 != bracket_a2:
        notes.append("Allele mistake")
    a1, a2 = bracket_a1, bracket_a2

    fr, gc_mode, h, kkf, kkr, early_failed = _choose_initial_direction(flank, kk1, kk3, a1, a2)
    if early_failed:
        return _failed_result(snp_id, a1, a2, notes)

    # Pad flanking sequence with 'Q' to >= 70 bp per side
    if len(flank) - kk3 < 70:
        flank += "Q" * (70 - (len(flank) - kk3))
    if kk1 < 70:
        flank = ("Q" * (70 - kk1)) + flank

    kk1 = _vba_instr(flank, "[")
    kk3 = _vba_instr(flank, "]")

    m2 = 1
    if len(a1) == 1 and len(a2) == 1:
        m2 = 0 if (a1 + a2) in {"AT", "GC", "TA", "CG"} else 3

    direction = "forward" if fr in {0, 1} else "reverse"
    c5, c6, c7 = "", "", ""

    if fr in {0, 1}:
        c5, c6, c7 = _design_forward(flank, kk1, kk3, a1, a2, m2, h, kkf, trim_c6_tttt=True)
        direction = "forward"
        if c5 == "Failed" and (gc_mode == 1 or (0 < kkf <= kkr)):
            fr = -1

    if fr == -1:
        c5, c6, c7 = _design_reverse(flank, kk1, kk3, a1, a2, m2, h, kkr)
        direction = "reverse"

    if (gc_mode == 2 or (kkf > kkr and fr == -1 and kkf < 4)) and c5 == "Failed":
        c5, c6, c7 = _design_forward(flank, kk1, kk3, a1, a2, m2, h, kkf, trim_c6_tttt=False)
        direction = "forward"

    if c5 and c6 and c5 != "Failed" and _vba_right(c5, 5) == _vba_right(c6, 5):
        c5 = "Failed"

    if c5 == "Failed" or not c5 or not c6 or not c7:
        return _failed_result(snp_id, a1, a2, notes)

    if c5.startswith("TTTTT"):
        c5 = _vba_mid(c5, 2, 38)
    if c6.startswith("TTTT"):
        c6 = _vba_mid(c6, 2, 38)
    if c6.startswith("TTTT"):
        c6 = _vba_mid(c6, 2, 38)

    return {
        "snp_id": str(snp_id),
        "allele_x": a1,
        "allele_y": a2,
        "primer_x": FAM_TAIL + c5,
        "primer_y": HEX_TAIL + c6,
        "primer_c": c7,
        "primer_x_no_tail": c5,
        "primer_y_no_tail": c6,
        "direction": direction,
        "tm_x": _primer_tm(c5),
        "tm_y": _primer_tm(c6),
        "tm_c": _primer_tm(c7),
        "status": "OK",
        "notes": notes,
    }
