"""Unit tests untuk CLI `backend/scripts/prepare_reference_fasta.py` (A63).

Semua pengujian hanya menggunakan mini-FASTA sintetis di `tmp_path`.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import tracemalloc

from app.services.primer_service import _read_fasta_region, build_fasta_index, load_fasta_index


def test_prepare_reference_fasta_ncbi_names_merges_split_chromosome_and_wraps(tmp_path: Path, capsys):
    from scripts.prepare_reference_fasta import main

    in_fa = tmp_path / "ncbi_mini.fna"
    out_fa = tmp_path / "garlic_chr_named.fa"

    # Sengaja tempatkan chromosome 7_2 SEBELUM 7_1 dan masukkan baris EOF tanpa '\n'
    seg7_2 = ("CCCCGGGG" * 12) + "CCCC"  # 100 bp
    seg1 = "ACGT" * 23 + "ACG"  # 95 bp
    seg7_1 = ("AAAATTTT" * 13) + "AAAAAT"  # 110 bp
    scaf = "GGCCAATT" * 6 + "GG"  # 50 bp
    unmapped = "ATATATAT" * 3 + "ATATAT"  # 30 bp

    raw_text = (
        f">CM025792.1 Allium sativum cultivar Ershuizao chromosome 7_2, whole genome shotgun sequence\n"
        f"{seg7_2[:50]}\n{seg7_2[50:]}\n"
        f">CM025785.1 Allium sativum cultivar Ershuizao chromosome 1, whole genome shotgun sequence\n"
        f"{seg1[:60]}\n{seg1[60:]}\n"
        f">CM025791.1 Allium sativum cultivar Ershuizao chromosome 7_1, whole genome shotgun sequence\n"
        f"{seg7_1[:60]}\n{seg7_1[60:]}\n"
        f">JAAKQP020005934.1 Allium sativum cultivar Ershuizao scaffold5934, whole genome shotgun sequence\n"
        f"{scaf}\n"
        f">UNPLACED_99 mystery unplaced contig\n"
        f"{unmapped}"
    )
    in_fa.write_bytes(raw_text.encode("ascii"))

    rc = main(["--in", str(in_fa), "--out", str(out_fa), "--ncbi-names", "--wrap", "80"])
    assert rc == 0

    stdout = capsys.readouterr().out
    assert "chr7" in stdout
    assert "210" in stdout
    assert "unmapped" in stdout.lower()

    lines = out_fa.read_text(encoding="utf-8").splitlines()
    headers = [line for line in lines if line.startswith(">")]
    assert headers == [">chr7", ">chr1", ">scaffold5934", ">UNPLACED_99"]

    # Pastikan hasil dapat diindeks oleh primer_service.build_fasta_index dan dibaca Lintas-sambungan 7_1 -> 7_2
    fai_path = build_fasta_index(out_fa)
    assert fai_path.is_file()
    idx = load_fasta_index(out_fa)
    assert idx["by_exact"]["chr7"]["length"] == 210
    assert idx["by_exact"]["chr7"]["line_bases"] == 80
    assert idx["by_exact"]["chr7"]["line_width"] == 81

    # Sekuens chr7 harus berupa seg7_1 (110 bp) diikuti seg7_2 (100 bp) tanpa 'N' sisipan
    extracted_chr7 = _read_fasta_region(out_fa, "chr7", 1, 210, index=idx)
    assert extracted_chr7 == seg7_1 + seg7_2
    junction = _read_fasta_region(out_fa, "chr7", 105, 116, index=idx)
    assert junction == (seg7_1 + seg7_2)[104:116]

    assert _read_fasta_region(out_fa, "chr1", 1, 95, index=idx) == seg1
    assert _read_fasta_region(out_fa, "scaffold5934", 1, 50, index=idx) == scaf
    assert _read_fasta_region(out_fa, "UNPLACED_99", 1, 30, index=idx) == unmapped
    assert out_fa.read_bytes().endswith(b"\n")


def test_prepare_reference_fasta_explicit_map_tsv_and_gz_streaming(tmp_path: Path):
    from scripts.prepare_reference_fasta import main

    in_gz = tmp_path / "mini.fa.gz"
    out_fa = tmp_path / "mapped.fa"
    map_tsv = tmp_path / "mapping.tsv"

    ctg_b = "C" * 50
    ctg_a = "A" * 70
    ctg_c = "G" * 40

    with gzip.open(in_gz, "wt", encoding="utf-8") as fh:
        fh.write(f">ctgB second part\n{ctg_b}\n")
        fh.write(f">ctgA first part\n{ctg_a}\n")
        fh.write(f">ctgC extra\n{ctg_c}\n")

    map_tsv.write_text(
        "# old_name\tnew_name\n"
        "ctgA\tchr2\n"
        "ctgB\tchr2\n",
        encoding="utf-8",
    )

    rc = main(["--in", str(in_gz), "--out", str(out_fa), "--map", str(map_tsv), "--wrap", "40"])
    assert rc == 0

    idx = load_fasta_index(out_fa)
    assert idx["by_exact"]["chr2"]["length"] == 120
    assert idx["by_exact"]["ctgC"]["length"] == 40
    assert _read_fasta_region(out_fa, "chr2", 1, 120, index=idx) == ctg_a + ctg_b


def test_prepare_reference_fasta_vcf_contig_validation_pass_and_fail(tmp_path: Path, capsys):
    from scripts.prepare_reference_fasta import main

    in_fa = tmp_path / "mini_vcf.fa"
    out_fa = tmp_path / "out_vcf.fa"
    in_fa.write_text(
        ">CM025785.1 chromosome 1\n" + ("A" * 95) + "\n"
        ">CM025791.1 chromosome 7_1\n" + ("C" * 110) + "\n"
        ">CM025792.1 chromosome 7_2\n" + ("G" * 100) + "\n",
        encoding="utf-8",
    )

    vcf_ok = tmp_path / "ok.vcf"
    vcf_ok.write_text(
        "##fileformat=VCFv4.2\n"
        "##contig=<ID=chr1,length=95>\n"
        "##contig=<ID=chr7,length=210>\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n",
        encoding="utf-8",
    )
    assert main(["--in", str(in_fa), "--out", str(out_fa), "--ncbi-names", "--vcf", str(vcf_ok)]) == 0
    capsys.readouterr()

    vcf_bad_gz = tmp_path / "mismatch.vcf.gz"
    with gzip.open(vcf_bad_gz, "wt", encoding="utf-8") as fh:
        fh.write(
            "##fileformat=VCFv4.2\n"
            "##contig=<ID=chr1,length=95>\n"
            "##contig=<ID=chr7,length=999>\n"
            "##contig=<ID=chr99,length=100>\n"
            "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
        )
    rc_bad = main(["--in", str(in_fa), "--out", str(out_fa), "--ncbi-names", "--vcf", str(vcf_bad_gz)])
    assert rc_bad != 0
    out_msg = capsys.readouterr().out
    assert "chr7" in out_msg
    assert "chr99" in out_msg


def test_prepare_reference_fasta_low_memory_streaming(tmp_path: Path):
    from scripts.prepare_reference_fasta import main

    in_fa = tmp_path / "stream_12mb.fa"
    out_fa = tmp_path / "stream_out.fa"
    block = b"ACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGTACGT\n"  # 64 bp + \n
    with in_fa.open("wb") as fh:
        fh.write(b">CM000001.1 chromosome 1\n")
        for _ in range(180_000):  # ~11.5 MiB
            fh.write(block)

    tracemalloc.start()
    try:
        rc = main(["--in", str(in_fa), "--out", str(out_fa), "--ncbi-names", "--wrap", "80"])
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert rc == 0
    assert peak < 8 * 1024 * 1024, f"Peak memory terlalu tinggi untuk streaming: {peak} bytes"
    idx = load_fasta_index(out_fa)
    assert idx["by_exact"]["chr1"]["length"] == 180_000 * 64
