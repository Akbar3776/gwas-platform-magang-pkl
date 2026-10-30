import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from app.services.phenotype_service import (
    inspect_phenotype,
    load_phenotype_table,
    read_vcf_sample_ids,
)
from tests.fixtures.make_fixtures import ensure_fixtures


def test_phenotype_table_loading_and_delimiters():
    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Tab separated
        tsv_path = Path(tmp_dir) / "test.tsv"
        tsv_path.write_text("FID\tIID\tTRAIT\nFAM1\tIND1\t10.5\nFAM2\tIND2\t12.0\n", encoding="utf-8")
        df_tsv = load_phenotype_table(tsv_path)
        assert list(df_tsv.columns) == ["FID", "IID", "TRAIT"]
        assert len(df_tsv) == 2
        assert df_tsv["IID"].tolist() == ["IND1", "IND2"]

        # 2. Comma separated
        csv_path = Path(tmp_dir) / "test.csv"
        csv_path.write_text("FID,IID,TRAIT\nFAM1,IND1,10.5\nFAM2,IND2,12.0\n", encoding="utf-8")
        df_csv = load_phenotype_table(csv_path)
        assert list(df_csv.columns) == ["FID", "IID", "TRAIT"]
        assert len(df_csv) == 2

        # 3. Space separated
        space_path = Path(tmp_dir) / "test.txt"
        space_path.write_text("FID  IID   TRAIT\nFAM1 IND1  10.5\nFAM2 IND2  12.0\n", encoding="utf-8")
        df_space = load_phenotype_table(space_path)
        assert list(df_space.columns) == ["FID", "IID", "TRAIT"]
        assert len(df_space) == 2


def test_inspect_phenotype_with_fixtures():
    vcf_path, pheno_path, _ = ensure_fixtures()
    vcf_ids = read_vcf_sample_ids(vcf_path)
    assert len(vcf_ids) == 120
    assert "SAMPLE_001" in vcf_ids

    df = load_phenotype_table(pheno_path)
    report = inspect_phenotype(
        vcf_ids=vcf_ids,
        df=df,
        trait="TRAIT1",
        id_column="IID",
    )

    # Acceptance criteria verification:
    # 120 VCF samples total
    assert report["n_vcf_samples"] == 120
    # Exactly 3 IDs that only exist in phenotype (EXTRA_001, EXTRA_002, EXTRA_003)
    assert report["only_in_pheno_count"] == 3
    # Exactly 1 duplicate IID (SAMPLE_001 repeated)
    assert len(report["pheno_duplicate_ids"]) == 1
    # Missing count (5 'NA' + 2 '-9' = 7)
    assert report["missing_count"] == 7
    # Exactly 1 non-numeric value ('INVALID_VALUE')
    assert report["non_numeric_count"] == 1
    assert any(item["value"] == "INVALID_VALUE" for item in report["non_numeric_examples"])

    # Matched samples should be around 120 - 7 missing - 1 invalid = 112
    assert report["n_matched"] == 112
    assert len(report["matched_ids"]) == 112

    # Stats, histogram and boxplot checks
    assert report["stats"]["N"] == 112
    assert report["stats"]["mean"] > 0
    assert len(report["histogram"]["edges"]) > 5
    assert len(report["histogram"]["counts"]) == len(report["histogram"]["edges"]) - 1
    assert "q1" in report["boxplot"]
    assert "median" in report["boxplot"]
    assert "q3" in report["boxplot"]


def test_inspect_phenotype_blocking_errors():
    vcf_ids = ["SAMPLE1", "SAMPLE2", "SAMPLE3"]
    df = pd.DataFrame({
        "IID": ["SAMPLE1", "SAMPLE2", "SAMPLE3"],
        "TRAIT_WRONG": ["1.0", "2.0", "3.0"],
    })

    # Missing trait column -> blocking error
    res = inspect_phenotype(vcf_ids, df, trait="TRAIT_NONEXISTENT", id_column="IID")
    assert len(res["blocking_errors"]) > 0
    assert any("TRAIT_NONEXISTENT" in err for err in res["blocking_errors"])

    # Too few matched samples (< 3)
    df_few = pd.DataFrame({
        "IID": ["SAMPLE1", "OTHER1"],
        "TRAIT": ["10.0", "20.0"],
    })
    res_few = inspect_phenotype(vcf_ids, df_few, trait="TRAIT", id_column="IID")
    assert len(res_few["blocking_errors"]) > 0
    assert any("< 3" in err for err in res_few["blocking_errors"])
