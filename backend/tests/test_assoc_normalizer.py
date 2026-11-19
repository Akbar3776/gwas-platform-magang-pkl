from app.services.assoc_normalizer import (
    CANONICAL_COLUMNS,
    detect_association_format,
    normalize_association_results,
)


def test_detect_association_format():
    gemma_header = ["chr", "rs", "ps", "n_miss", "allele1", "allele0", "af", "beta", "se", "p_wald"]
    assert detect_association_format(gemma_header) == "gemma"

    plink2_header = ["#CHROM", "POS", "ID", "REF", "ALT", "A1", "TEST", "OBS_CT", "BETA", "SE", "T_STAT", "P"]
    assert detect_association_format(plink2_header) == "plink2"

    gapit_header = ["SNP", "Chromosome", "Position", "P.value", "maf", "nobs", "H&B.P.Value", "Effect"]
    assert detect_association_format(gapit_header) == "gapit"


def test_normalize_gemma_output(tmp_path):
    gemma_file = tmp_path / "test.assoc.txt"
    gemma_content = (
        "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tp_wald\n"
        "1\trs101\t1000\t0\tA\tG\t0.25\t0.15\t0.03\t1.2e-6\n"
        "2\trs102\t2000\t1\tT\tC\t0.10\t-0.20\t0.05\t5.0e-5\n"
    )
    gemma_file.write_text(gemma_content, encoding="utf-8")

    out_tsv = tmp_path / "gwas_results.normalized.tsv"
    count = normalize_association_results(gemma_file, out_tsv)
    assert count == 2

    lines = out_tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    header = lines[0].split("\t")
    assert header == CANONICAL_COLUMNS

    row1 = lines[1].split("\t")
    assert row1[0] == "1"
    assert row1[1] == "1000"
    assert row1[2] == "rs101"
    assert row1[3] == "A"
    assert row1[4] == "G"
    assert float(row1[8]) == 1.2e-6


def test_normalize_plink2_output(tmp_path):
    plink_file = tmp_path / "test.glm.linear"
    plink_content = (
        "#CHROM\tPOS\tID\tREF\tALT\tA1\tTEST\tN_OBS\tBETA\tSE\tP\n"
        "1\t5000\t1:5000\tG\tA\tA\tADD\t150\t0.35\t0.08\t2.5e-8\n"
        "1\t5000\t1:5000\tG\tA\tA\tPC1\t150\t0.01\t0.02\t0.45\n"  # Covariate line should be filtered
        "3\t9000\t3:9000\tC\tT\tT\tADD\t150\t-0.12\t0.04\t1.0e-3\n"
    )
    plink_file.write_text(plink_content, encoding="utf-8")

    out_tsv = tmp_path / "plink_normalized.tsv"
    count = normalize_association_results(plink_file, out_tsv)
    assert count == 2

    lines = out_tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    row1 = lines[1].split("\t")
    assert row1[0] == "1"
    assert row1[1] == "5000"
    assert row1[2] == "1:5000"
    assert row1[3] == "A"
    assert float(row1[8]) == 2.5e-8


def test_normalize_gapit_output(tmp_path):
    gapit_file = tmp_path / "test.csv"
    gapit_content = (
        '"SNP","Chromosome","Position","P.value","maf","nobs","Effect"\n'
        '"rs501",1,12000,3.4e-7,0.30,120,0.45\n'
        '"rs502",2,15000,8.1e-4,0.15,120,-0.22\n'
    )
    gapit_file.write_text(gapit_content, encoding="utf-8")

    out_tsv = tmp_path / "gapit_normalized.tsv"
    count = normalize_association_results(gapit_file, out_tsv)
    assert count == 2

    lines = out_tsv.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    row1 = lines[1].split("\t")
    assert row1[0] == "1"
    assert row1[1] == "12000"
    assert row1[2] == "rs501"
    assert float(row1[8]) == 3.4e-7


def test_normalize_gapit_4_1_headers_and_chr_prefix(tmp_path):
    import csv
    import pytest

    gapit_file = tmp_path / "GAPIT.Association.GWAS_Results.FarmCPU.TRAIT(NYC).csv"
    gapit_content = (
        "SNP,Chr,Pos,P.value,MAF,effect,nobs,H&B.P.Value\n"
        "snp1,1,1000,0.42,0.48,0.11,80,0.99\n"
        "snp2,H1,2000,0.15,0.25,-0.05,80,0.75\n"
    )
    gapit_file.write_text(gapit_content, encoding="utf-8")

    out_tsv = tmp_path / "gapit41_normalized.tsv"
    count = normalize_association_results(gapit_file, out_tsv)
    assert count == 2

    with out_tsv.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))

    assert len(rows) == 2
    assert rows[0]["chr"] == "1"
    assert rows[0]["pos"] == "1000"
    assert float(rows[0]["p"]) == pytest.approx(0.42)
    assert float(rows[0]["af"]) == pytest.approx(0.48)
    assert float(rows[0]["beta"]) == pytest.approx(0.11)
    assert rows[0]["n"] == "80"

    assert rows[1]["chr"] == "H1"


def test_normalize_assoc_preserves_non_chr_prefixes_across_formats(tmp_path):
    from app.services.assoc_normalizer import normalize_association_results

    gemma_assoc = tmp_path / "gemma.assoc.txt"
    gemma_assoc.write_text(
        "\t".join(["chr", "rs", "ps", "n_miss", "allele1", "allele0", "af", "beta", "se", "p_wald"]) + "\n"
        + "\n".join([
            "Ca1\trs_ca1\t100\t0\tA\tG\t0.2\t0.5\t0.1\t1e-8",
            "contig_12\trs_ctg\t200\t0\tC\tT\t0.3\t0.4\t0.1\t2e-7",
            "chrC\trs_chrc\t300\t0\tG\tA\t0.1\t0.3\t0.1\t3e-6",
            "Chr01\trs_chr01\t400\t0\tT\tC\t0.4\t0.2\t0.1\t4e-5",
            "chrUn_001\trs_un\t500\t0\tA\tT\t0.25\t0.1\t0.1\t5e-4",
        ]) + "\n",
        encoding="utf-8",
    )

    out_path = tmp_path / "norm.tsv"
    rows_written = normalize_association_results(gemma_assoc, out_path)
    assert rows_written == 5

    lines = out_path.read_text(encoding="utf-8").splitlines()
    chrs = [line.split("\t")[0] for line in lines[1:]]
    assert chrs == ["Ca1", "contig_12", "C", "01", "Un_001"]

    plink_assoc = tmp_path / "plink2.glm.linear"
    plink_assoc.write_text(
        "\t".join(["#CHROM", "POS", "ID", "REF", "ALT", "A1", "A1_FREQ", "BETA", "SE", "P", "OBS_CT"]) + "\n"
        + "Ca1\t100\trs_ca1\tG\tA\tA\t0.2\t0.5\t0.1\t1e-8\t100\n"
        + "chrC\t300\trs_chrc\tA\tG\tG\t0.1\t0.3\t0.1\t3e-6\t100\n",
        encoding="utf-8",
    )
    plink_out = tmp_path / "plink_norm.tsv"
    assert normalize_association_results(plink_assoc, plink_out) == 2
    plink_chrs = [line.split("\t")[0] for line in plink_out.read_text(encoding="utf-8").splitlines()[1:]]
    assert plink_chrs == ["Ca1", "C"]

    canon_assoc = tmp_path / "canon.tsv"
    canon_assoc.write_text(
        "chr\tpos\trs\ta1\ta0\taf\tbeta\tse\tp\tn\n"
        "Ca1\t100\trs_ca1\tA\tG\t0.2\t0.5\t0.1\t1e-8\t100\n"
        "chrH1\t200\trs_h1\tC\tT\t0.3\t0.4\t0.1\t2e-7\t100\n",
        encoding="utf-8",
    )
    canon_out = tmp_path / "canon_norm.tsv"
    assert normalize_association_results(canon_assoc, canon_out) == 2
    canon_chrs = [line.split("\t")[0] for line in canon_out.read_text(encoding="utf-8").splitlines()[1:]]
    assert canon_chrs == ["Ca1", "H1"]


def test_normalize_gemma_n_column_uses_fam_sample_count_or_na(tmp_path):
    import csv

    # 1. Without .fam next to assoc output or explicit fam_path -> n must be NA (not n_miss=0/2)
    no_fam_assoc = tmp_path / "standalone.assoc.txt"
    no_fam_assoc.write_text(
        "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tp_wald\n"
        "1\trs1\t1000\t0\tA\tG\t0.25\t0.15\t0.03\t1.2e-6\n"
        "1\trs2\t2000\t2\tT\tC\t0.10\t-0.20\t0.05\t5.0e-5\n",
        encoding="utf-8",
    )
    out_na = tmp_path / "standalone_norm.tsv"
    assert normalize_association_results(no_fam_assoc, out_na) == 2
    with out_na.open("r", encoding="utf-8") as fh:
        rows_na = list(csv.DictReader(fh, delimiter="\t"))
    assert [r["n"] for r in rows_na] == ["NA", "NA"]

    # 2. With GEMMA workspace layout (work_dir/genotypes.fam and work_dir/output/association.assoc.txt)
    work_dir = tmp_path / "gemma_work"
    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True)
    fam_file = work_dir / "genotypes.fam"
    fam_file.write_text(
        "\n".join(f"F{i} I{i} 0 0 0 {1.0 + i}" for i in range(5)) + "\n",
        encoding="utf-8",
    )
    assoc_file = output_dir / "association.assoc.txt"
    assoc_file.write_text(
        "chr\trs\tps\tn_miss\tallele1\tallele0\taf\tbeta\tse\tp_wald\n"
        "1\trs10\t1000\t0\tA\tG\t0.25\t0.15\t0.03\t1.2e-6\n"
        "1\trs11\t2000\t2\tT\tC\t0.10\t-0.20\t0.05\t5.0e-5\n",
        encoding="utf-8",
    )
    out_fam = tmp_path / "gemma_norm.tsv"
    assert normalize_association_results(assoc_file, out_fam) == 2
    with out_fam.open("r", encoding="utf-8") as fh:
        rows_fam = list(csv.DictReader(fh, delimiter="\t"))
    assert [r["n"] for r in rows_fam] == ["5", "3"]

    # 3. Explicit fam_path overrides missing auto-detection
    custom_fam = tmp_path / "custom_cohort.fam"
    custom_fam.write_text("\n".join(f"F{i} I{i} 0 0 0 1" for i in range(8)) + "\n", encoding="utf-8")
    out_explicit = tmp_path / "explicit_norm.tsv"
    assert normalize_association_results(no_fam_assoc, out_explicit, fam_path=custom_fam) == 2
    with out_explicit.open("r", encoding="utf-8") as fh:
        rows_exp = list(csv.DictReader(fh, delimiter="\t"))
    assert [r["n"] for r in rows_exp] == ["8", "6"]
