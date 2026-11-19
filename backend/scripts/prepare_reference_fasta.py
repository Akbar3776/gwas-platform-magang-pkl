"""Siapkan FASTA referensi untuk GWAS Primer/Flanking secara streaming hemat memori.

Fitur:
- Membaca FASTA biasa atau `.gz` dalam potongan O(1) RAM (aman untuk genom belasan GB).
- Mengganti nama dan/atau menggabungkan contig (mis. `chromosome 7_1` + `chromosome 7_2` -> `chr7`)
  menggunakan `--map mapping.tsv` dan/atau `--ncbi-names` tanpa menyisipkan huruf `N`.
- Membungkus ulang panjang baris sekuens (`--wrap`, default 80) dengan newline konsisten di EOF.
- Memverifikasi kesesuaian contig dan panjang terhadap header `##contig` berkas `--vcf` (`.vcf`/`.vcf.gz`).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
from pathlib import Path
import re
import tempfile
from typing import Sequence

_CHUNK_BYTES = 1 << 20  # 1 MiB streaming buffer
_SPLIT_CHR_RE = re.compile(r"\bchromosome\s+([0-9A-Za-z]+)_(\d+)\b", re.IGNORECASE)
_WHOLE_CHR_RE = re.compile(r"\bchromosome\s+([0-9A-Za-z]+)\b", re.IGNORECASE)
_SCAFFOLD_RE = re.compile(r"\bscaffold[_\s-]?(\d+)\b", re.IGNORECASE)
_ALREADY_NAMED_RE = re.compile(r"^(?:chr(?:[0-9A-Za-z]+)|scaffold\d+)$", re.IGNORECASE)
_VCF_CONTIG_RE = re.compile(r"^##contig=<([^>]+)>")
_VCF_ID_RE = re.compile(r"(?:^|,)ID=([^,>]+)")
_VCF_LENGTH_RE = re.compile(r"(?:^|,)length=(\d+)")


def _chr_label(raw: str) -> str:
    label = re.sub(r"^chr", "", str(raw).strip(), flags=re.IGNORECASE)
    return label or "0"


def _is_gzip_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(2) == b"\x1f\x8b"
    except OSError:
        return False


@dataclass
class _SourceContig:
    index: int
    old_id: str
    header_text: str
    data_start: int
    data_end: int
    base_len: int


@dataclass
class _PlannedSource:
    contig: _SourceContig
    part_order: int


@dataclass
class _OutputGroup:
    new_name: str
    sources: list[_PlannedSource]
    total_len: int
    mapped: bool


def _parse_header_text(header_bytes: bytes, contig_idx: int) -> tuple[str, str]:
    header_text = header_bytes.strip().decode("utf-8", errors="replace")
    tokens = header_text.split()
    if not tokens:
        raise ValueError(f"Header FASTA kosong ('>') pada contig ke-{contig_idx + 1}.")
    return tokens[0], header_text


def _scan_plain_fasta(path: Path) -> list[_SourceContig]:
    contigs: list[_SourceContig] = []
    cur_idx = -1
    cur_old_id = ""
    cur_header = ""
    cur_start = 0
    cur_end = 0
    cur_bases = 0

    in_header = False
    header_buf = bytearray()
    at_line_start = True
    file_offset = 0

    def _close_current() -> None:
        if cur_idx >= 0:
            contigs.append(
                _SourceContig(
                    index=cur_idx,
                    old_id=cur_old_id,
                    header_text=cur_header,
                    data_start=cur_start,
                    data_end=cur_end,
                    base_len=cur_bases,
                )
            )

    with path.open("rb") as fh:
        while True:
            buf = fh.read(_CHUNK_BYTES)
            if not buf:
                break
            chunk_start = file_offset
            file_offset += len(buf)
            cursor = 0
            buf_len = len(buf)

            while cursor < buf_len:
                if in_header:
                    nl_pos = buf.find(b"\n", cursor)
                    if nl_pos == -1:
                        if len(header_buf) < 4096:
                            end_hdr = cursor + (4096 - len(header_buf))
                            header_buf.extend(buf[cursor:end_hdr])
                        cursor = buf_len
                    else:
                        if len(header_buf) < 4096:
                            take = min(nl_pos - cursor, 4096 - len(header_buf))
                            end_hdr = cursor + take
                            header_buf.extend(buf[cursor:end_hdr])
                        cur_idx += 1
                        cur_old_id, cur_header = _parse_header_text(bytes(header_buf), cur_idx)
                        cur_start = chunk_start + nl_pos + 1
                        cur_end = cur_start
                        cur_bases = 0
                        in_header = False
                        at_line_start = True
                        cursor = nl_pos + 1
                    continue

                if at_line_start and buf[cursor:cursor + 1] == b">":
                    _close_current()
                    header_buf.clear()
                    in_header = True
                    at_line_start = False
                    cursor += 1
                    continue

                nl_gt = buf.find(b"\n>", cursor)
                if nl_gt == -1:
                    seg_end = buf_len
                    next_cursor = buf_len
                    next_line_start = buf[buf_len - 1:buf_len] == b"\n"
                else:
                    seg_end = nl_gt + 1
                    next_cursor = nl_gt + 1
                    next_line_start = True

                if cur_idx >= 0 and seg_end > cursor:
                    cr_count = buf.count(b"\r", cursor, seg_end)
                    nl_count = buf.count(b"\n", cursor, seg_end)
                    cur_bases += (seg_end - cursor) - cr_count - nl_count
                    cur_end = chunk_start + seg_end

                cursor = next_cursor
                at_line_start = next_line_start

    if in_header:
        cur_idx += 1
        cur_old_id, cur_header = _parse_header_text(bytes(header_buf), cur_idx)
        cur_start = file_offset
        cur_end = file_offset
        cur_bases = 0

    _close_current()
    if not contigs:
        raise ValueError(f"Tidak ada contig FASTA valid ditemukan pada '{path}'.")
    return contigs


def _scan_gzip_fasta(path: Path) -> list[_SourceContig]:
    contigs: list[_SourceContig] = []
    cur_idx = -1
    cur_old_id = ""
    cur_header = ""
    cur_bases = 0

    with gzip.open(path, "rb") as fh:
        while True:
            raw_line = fh.readline(_CHUNK_BYTES)
            if not raw_line:
                break
            if raw_line.startswith(b">"):
                if cur_idx >= 0:
                    contigs.append(
                        _SourceContig(
                            index=cur_idx,
                            old_id=cur_old_id,
                            header_text=cur_header,
                            data_start=0,
                            data_end=0,
                            base_len=cur_bases,
                        )
                    )
                hdr_buf = bytearray(raw_line[1:4096])
                while not raw_line.endswith(b"\n"):
                    raw_line = fh.readline(_CHUNK_BYTES)
                    if not raw_line:
                        break
                    if len(hdr_buf) < 4096:
                        hdr_buf.extend(raw_line[: 4096 - len(hdr_buf)])
                cur_idx += 1
                cur_old_id, cur_header = _parse_header_text(bytes(hdr_buf), cur_idx)
                cur_bases = 0
                continue

            if cur_idx < 0:
                continue

            cr_count = raw_line.count(b"\r")
            nl_count = raw_line.count(b"\n")
            cur_bases += len(raw_line) - cr_count - nl_count

    if cur_idx >= 0:
        contigs.append(
            _SourceContig(
                index=cur_idx,
                old_id=cur_old_id,
                header_text=cur_header,
                data_start=0,
                data_end=0,
                base_len=cur_bases,
            )
        )

    if not contigs:
        raise ValueError(f"Tidak ada contig FASTA valid ditemukan pada '{path}'.")
    return contigs


def _load_tsv_mapping(map_path: Path) -> tuple[dict[str, tuple[str, int]], dict[str, int]]:
    if not map_path.is_file():
        raise FileNotFoundError(f"File mapping tidak ditemukan: {map_path}")
    by_old: dict[str, tuple[str, int]] = {}
    group_order: dict[str, int] = {}
    for row_idx, raw in enumerate(map_path.read_text(encoding="utf-8", errors="replace").splitlines()):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = stripped.split("\t")
        if len(cols) < 2:
            cols = stripped.split()
        if len(cols) < 2:
            raise ValueError(f"Baris mapping tidak valid pada baris {row_idx + 1}: {raw!r}")
        old_name, new_name = cols[0].strip(), cols[1].strip()
        if old_name and new_name:
            by_old[old_name] = (new_name, row_idx)
            group_order.setdefault(new_name, len(group_order))
    return by_old, group_order


def _infer_ncbi_target(contig: _SourceContig) -> tuple[str, int, bool]:
    m_split = _SPLIT_CHR_RE.search(contig.header_text)
    if m_split:
        return f"chr{m_split.group(1)}", int(m_split.group(2)), True
    m_chr = _WHOLE_CHR_RE.search(contig.header_text)
    if m_chr:
        return f"chr{m_chr.group(1)}", 0, True
    m_scaf = _SCAFFOLD_RE.search(contig.header_text)
    if m_scaf:
        return f"scaffold{m_scaf.group(1)}", 0, True
    if _ALREADY_NAMED_RE.match(contig.old_id):
        return contig.old_id, 0, True
    return contig.old_id, 0, False


def _build_output_plan(
    contigs: list[_SourceContig],
    *,
    tsv_map: dict[str, tuple[str, int]] | None,
    tsv_group_order: dict[str, int] | None,
    use_ncbi_names: bool,
) -> tuple[list[_OutputGroup], int]:
    groups_map: dict[str, _OutputGroup] = {}
    explicit_targets: list[str] = []
    auto_targets: list[str] = []
    unmapped_count = 0

    for contig in contigs:
        if tsv_map and contig.old_id in tsv_map:
            new_name, order_idx = tsv_map[contig.old_id]
            mapped = True
            bucket = explicit_targets
        elif use_ncbi_names:
            new_name, order_idx, mapped = _infer_ncbi_target(contig)
            bucket = auto_targets
            if not mapped:
                unmapped_count += 1
        else:
            new_name = contig.old_id
            order_idx = contig.index
            mapped = tsv_map is None
            bucket = auto_targets
            if tsv_map is not None:
                unmapped_count += 1

        if new_name not in groups_map:
            groups_map[new_name] = _OutputGroup(new_name=new_name, sources=[], total_len=0, mapped=mapped)
            bucket.append(new_name)
        grp = groups_map[new_name]
        grp.sources.append(_PlannedSource(contig=contig, part_order=order_idx))
        grp.total_len += contig.base_len

    for grp in groups_map.values():
        grp.sources.sort(key=lambda s: (s.part_order, s.contig.index))

    if tsv_group_order:
        explicit_targets.sort(key=lambda name: tsv_group_order.get(name, 10**9))

    ordered_groups = [groups_map[name] for name in explicit_targets + auto_targets]
    return ordered_groups, unmapped_count


def _write_wrapped_chunk(out_fh, carry: bytearray, clean_bytes: bytes, wrap: int) -> None:
    pos = 0
    total = len(clean_bytes)
    if total == 0:
        return

    if carry:
        need = wrap - len(carry)
        if total < need:
            carry.extend(clean_bytes)
            return
        carry.extend(clean_bytes[:need])
        out_fh.write(carry)
        out_fh.write(b"\n")
        carry.clear()
        pos = need

    while pos + wrap <= total:
        end_pos = pos + wrap
        out_fh.write(clean_bytes[pos:end_pos])
        out_fh.write(b"\n")
        pos = end_pos

    if pos < total:
        carry.extend(clean_bytes[pos:])


def _stream_plain_output(in_path: Path, tmp_out: Path, plan: list[_OutputGroup], wrap: int) -> None:
    carry = bytearray()
    with in_path.open("rb") as fin, tmp_out.open("wb") as fout:
        for grp in plan:
            fout.write(f">{grp.new_name}\n".encode("utf-8"))
            carry.clear()
            for src in grp.sources:
                c = src.contig
                fin.seek(c.data_start)
                remaining = c.data_end - c.data_start
                while remaining > 0:
                    raw = fin.read(min(_CHUNK_BYTES, remaining))
                    if not raw:
                        break
                    remaining -= len(raw)
                    clean = raw.translate(None, b"\r\n")
                    _write_wrapped_chunk(fout, carry, clean, wrap)
            if carry:
                fout.write(carry)
                fout.write(b"\n")
                carry.clear()


def _emit_spooled_group(
    fout,
    grp: _OutputGroup,
    spool_paths: dict[int, Path],
    wrap: int,
) -> None:
    fout.write(f">{grp.new_name}\n".encode("utf-8"))
    carry = bytearray()
    for src in grp.sources:
        spool_file = spool_paths.pop(src.contig.index)
        try:
            with spool_file.open("rb") as sfh:
                while True:
                    chunk = sfh.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    _write_wrapped_chunk(fout, carry, chunk, wrap)
        finally:
            spool_file.unlink(missing_ok=True)
    if carry:
        fout.write(carry)
        fout.write(b"\n")


def _stream_gzip_output(in_path: Path, tmp_out: Path, plan: list[_OutputGroup], wrap: int) -> None:
    needed_order = [src.contig.index for grp in plan for src in grp.sources]
    is_monotonic = all(needed_order[i] < needed_order[i + 1] for i in range(len(needed_order) - 1))

    if is_monotonic:
        idx_to_group_pos: dict[int, tuple[int, int]] = {}
        for g_idx, grp in enumerate(plan):
            for s_pos, src in enumerate(grp.sources):
                idx_to_group_pos[src.contig.index] = (g_idx, s_pos)

        carry = bytearray()
        cur_idx = -1
        with gzip.open(in_path, "rb") as fin, tmp_out.open("wb") as fout:
            cur_grp_idx = -1
            while True:
                line = fin.readline(_CHUNK_BYTES)
                if not line:
                    break
                if line.startswith(b">"):
                    if cur_grp_idx >= 0 and idx_to_group_pos[cur_idx][1] == len(plan[cur_grp_idx].sources) - 1:
                        if carry:
                            fout.write(carry)
                            fout.write(b"\n")
                            carry.clear()
                    while not line.endswith(b"\n"):
                        line = fin.readline(_CHUNK_BYTES)
                        if not line:
                            break
                    cur_idx += 1
                    cur_grp_idx, s_pos = idx_to_group_pos[cur_idx]
                    if s_pos == 0:
                        fout.write(f">{plan[cur_grp_idx].new_name}\n".encode("utf-8"))
                        carry.clear()
                    continue

                if cur_idx < 0:
                    continue
                clean = line.translate(None, b"\r\n")
                _write_wrapped_chunk(fout, carry, clean, wrap)

            if carry:
                fout.write(carry)
                fout.write(b"\n")
        return

    with tempfile.TemporaryDirectory(dir=tmp_out.parent) as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        spool_paths: dict[int, Path] = {}
        required_for_group = [{src.contig.index for src in grp.sources} for grp in plan]
        next_emit_idx = 0

        with gzip.open(in_path, "rb") as fin, tmp_out.open("wb") as fout:
            cur_idx = -1
            cur_spool_fh = None
            try:
                while True:
                    line = fin.readline(_CHUNK_BYTES)
                    if not line:
                        break
                    if line.startswith(b">"):
                        if cur_spool_fh is not None:
                            cur_spool_fh.close()
                            cur_spool_fh = None
                            while (
                                next_emit_idx < len(plan)
                                and required_for_group[next_emit_idx].issubset(spool_paths)
                            ):
                                _emit_spooled_group(fout, plan[next_emit_idx], spool_paths, wrap)
                                next_emit_idx += 1
                        while not line.endswith(b"\n"):
                            line = fin.readline(_CHUNK_BYTES)
                            if not line:
                                break
                        cur_idx += 1
                        spool_file = tmp_dir / f"seg_{cur_idx}.bin"
                        spool_paths[cur_idx] = spool_file
                        cur_spool_fh = spool_file.open("wb")
                        continue

                    if cur_spool_fh is not None:
                        cur_spool_fh.write(line.translate(None, b"\r\n"))
            finally:
                if cur_spool_fh is not None:
                    cur_spool_fh.close()

            while next_emit_idx < len(plan):
                _emit_spooled_group(fout, plan[next_emit_idx], spool_paths, wrap)
                next_emit_idx += 1


def _parse_vcf_contigs(vcf_path: Path) -> list[tuple[str, int | None]]:
    if not vcf_path.is_file():
        raise FileNotFoundError(f"File VCF tidak ditemukan: {vcf_path}")
    open_fn = gzip.open if _is_gzip_file(vcf_path) else open
    entries: list[tuple[str, int | None]] = []
    with open_fn(vcf_path, "rt", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            if not raw_line.startswith("#"):
                break
            m_contig = _VCF_CONTIG_RE.match(raw_line.strip())
            if not m_contig:
                continue
            body = m_contig.group(1)
            m_id = _VCF_ID_RE.search(body)
            if not m_id:
                continue
            cid = m_id.group(1).strip()
            m_len = _VCF_LENGTH_RE.search(body)
            clen = int(m_len.group(1)) if m_len else None
            entries.append((cid, clen))
    return entries


def _validate_against_vcf(vcf_path: Path, plan: list[_OutputGroup]) -> list[str]:
    vcf_contigs = _parse_vcf_contigs(vcf_path)
    if not vcf_contigs:
        return []

    by_exact = {grp.new_name: grp.total_len for grp in plan}
    by_norm: dict[str, tuple[str, int]] = {}
    for grp in plan:
        by_norm.setdefault(_chr_label(grp.new_name), (grp.new_name, grp.total_len))

    mismatches: list[str] = []
    for cid, expected_len in vcf_contigs:
        if cid in by_exact:
            matched_name = cid
            fasta_len = by_exact[cid]
        else:
            norm_hit = by_norm.get(_chr_label(cid))
            if norm_hit is None:
                mismatches.append(f"Contig VCF '{cid}' tidak ditemukan di FASTA output.")
                continue
            matched_name, fasta_len = norm_hit

        if expected_len is not None and fasta_len != expected_len:
            mismatches.append(
                f"Selisih panjang '{cid}' (FASTA '{matched_name}'): "
                f"FASTA={fasta_len:,} bp vs VCF={expected_len:,} bp."
            )
    return mismatches


def prepare_reference_fasta(
    *,
    input_fasta: str | Path,
    output_fasta: str | Path,
    map_tsv: str | Path | None = None,
    ncbi_names: bool = False,
    wrap: int = 80,
    vcf_path: str | Path | None = None,
) -> dict[str, object]:
    """Memproses dan membungkus ulang FASTA referensi secara streaming."""
    if wrap < 1:
        raise ValueError("--wrap harus >= 1.")

    in_path = Path(input_fasta)
    out_path = Path(output_fasta)
    if not in_path.is_file():
        raise FileNotFoundError(f"File FASTA input tidak ditemukan: {in_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tsv_map: dict[str, tuple[str, int]] | None = None
    tsv_order: dict[str, int] | None = None
    if map_tsv is not None:
        tsv_map, tsv_order = _load_tsv_mapping(Path(map_tsv))

    is_gz = _is_gzip_file(in_path)
    contigs = _scan_gzip_fasta(in_path) if is_gz else _scan_plain_fasta(in_path)
    plan, unmapped_count = _build_output_plan(
        contigs,
        tsv_map=tsv_map,
        tsv_group_order=tsv_order,
        use_ncbi_names=ncbi_names,
    )

    tmp_out = out_path.with_name(f".{out_path.name}.tmp")
    try:
        if is_gz:
            _stream_gzip_output(in_path, tmp_out, plan, wrap)
        else:
            _stream_plain_output(in_path, tmp_out, plan, wrap)
        tmp_out.replace(out_path)
    except Exception:
        tmp_out.unlink(missing_ok=True)
        raise

    vcf_mismatches: list[str] = []
    if vcf_path is not None:
        vcf_mismatches = _validate_against_vcf(Path(vcf_path), plan)

    return {
        "output_path": str(out_path),
        "entries": [
            {
                "new_name": grp.new_name,
                "length_bp": grp.total_len,
                "sources": [src.contig.old_id for src in grp.sources],
                "mapped": grp.mapped,
            }
            for grp in plan
        ],
        "unmapped_count": unmapped_count,
        "vcf_mismatches": vcf_mismatches,
        "exit_code": 1 if vcf_mismatches else 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Siapkan FASTA referensi untuk pipeline GWAS (rename, merge, re-wrap streaming, validasi VCF)."
    )
    parser.add_argument("--in", dest="input_fasta", required=True, help="Path FASTA input (.fa/.fna/.fasta/.gz)")
    parser.add_argument("--out", dest="output_fasta", required=True, help="Path FASTA output yang sudah dirapikan")
    parser.add_argument("--map", dest="map_tsv", default=None, help="File TSV pemetaan 2 kolom: old_name<TAB>new_name")
    parser.add_argument(
        "--ncbi-names",
        action="store_true",
        help="Petakan otomatis header NCBI 'chromosome N' / 'chromosome N_k' -> chrN & 'scaffoldM' -> scaffoldM",
    )
    parser.add_argument("--wrap", type=int, default=80, help="Lebar pembungkusan baris FASTA (default: 80)")
    parser.add_argument("--vcf", dest="vcf_path", default=None, help="File .vcf/.vcf.gz untuk verifikasi ##contig length")

    args = parser.parse_args(argv)
    summary = prepare_reference_fasta(
        input_fasta=args.input_fasta,
        output_fasta=args.output_fasta,
        map_tsv=args.map_tsv,
        ncbi_names=args.ncbi_names,
        wrap=args.wrap,
        vcf_path=args.vcf_path,
    )

    entries = summary["entries"]
    print(f"FASTA output: {summary['output_path']} ({len(entries)} contig, unmapped: {summary['unmapped_count']})")
    for item in entries:
        src_desc = " + ".join(item["sources"])
        print(f"  {item['new_name']}\t{item['length_bp']} bp\t<= {src_desc}")

    mismatches = summary["vcf_mismatches"]
    if mismatches:
        print("KETIDAKCOCOKAN CONTIG VCF:")
        for msg in mismatches:
            print(f"  - {msg}")
        return 1
    if args.vcf_path:
        print("Validasi ##contig VCF: SEMUA COCOK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
