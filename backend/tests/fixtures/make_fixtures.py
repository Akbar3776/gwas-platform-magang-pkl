"""Deterministic test fixture generator for GWAS pipeline integration tests.

Generates:
1. toy_gwas.vcf.gz: 120 samples, 3 chromosomes x 400 SNPs, 3 subpopulations,
   LD blocks, 1 duplicate pair, 2 related pairs, ~2% missingness.
2. toy_pheno.txt: Phenotype table with causal effect at 1:2500000 & 2:1200000,
   subpopulation effect, missing/invalid rows, extra IIDs, duplicate IID.
3. toy_annotation.gff3: GFF3 annotations with normalized chromosome names,
   containing gene for causal SNP 1 and gene ~30 kb from causal SNP 2.
"""

from __future__ import annotations

import gzip
from pathlib import Path
import numpy as np

FIXTURES_DIR = Path(__file__).resolve().parent / "data"


def ensure_fixtures(force: bool = False) -> tuple[Path, Path, Path]:
    """Ensure all test fixtures exist; generate them deterministically if not."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    vcf_path = FIXTURES_DIR / "toy_gwas.vcf.gz"
    pheno_path = FIXTURES_DIR / "toy_pheno.txt"
    gff_path = FIXTURES_DIR / "toy_annotation.gff3"

    if not force and vcf_path.is_file() and pheno_path.is_file() and gff_path.is_file():
        return vcf_path, pheno_path, gff_path

    rng = np.random.default_rng(42)
    n_samples = 120
    sample_ids = [f"SAMPLE_{i+1:03d}" for i in range(n_samples)]

    # Subpopulations: Pop1 (0..39), Pop2 (40..79), Pop3 (80..119)
    subpop = np.zeros(n_samples, dtype=int)
    subpop[40:80] = 1
    subpop[80:120] = 2

    chroms = [1, 2, 3]
    snps_per_chr = 400

    # Build variants and genotypes
    variants = []
    # Genotype array: shape (total_snps, n_samples), values 0, 1, 2 or -1 (missing)
    genotypes_list = []

    causal_1_idx = None
    causal_2_idx = None

    current_idx = 0
    for chrom in chroms:
        # 400 positions from 1,000,000 to 5,000,000
        positions = np.sort(rng.choice(np.arange(1_000_000, 5_000_000, 100), size=snps_per_chr, replace=False))
        # Ensure exact causal positions
        if chrom == 1:
            positions[150] = 2_500_000
            positions = np.sort(positions)
        elif chrom == 2:
            positions[50] = 1_200_000
            positions = np.sort(positions)

        # Base frequencies per subpopulation
        for pos in positions:
            snp_id = f"{chrom}:{pos}"
            if chrom == 1 and pos == 2_500_000:
                causal_1_idx = current_idx
                snp_id = "1:2500000"
            elif chrom == 2 and pos == 1_200_000:
                causal_2_idx = current_idx
                snp_id = "2:1200000"

            variants.append((chrom, int(pos), snp_id, "A", "G"))

            # Frequencies across the 3 populations
            base_p = rng.uniform(0.1, 0.9)
            p_pop = np.clip(
                [base_p - 0.15, base_p + 0.05, base_p + 0.10],
                0.05,
                0.95,
            )

            # Sample individual alleles with LD block effect
            g = np.zeros(n_samples, dtype=int)
            for i in range(n_samples):
                p = p_pop[subpop[i]]
                # 10% chance of inheriting from previous SNP if in same 10-SNP block
                if current_idx % 10 != 0 and current_idx > 0 and rng.random() < 0.65:
                    prev_val = genotypes_list[-1][i]
                    if prev_val != -1 and rng.random() < 0.85:
                        g[i] = prev_val
                    else:
                        a1 = int(rng.random() < p)
                        a2 = int(rng.random() < p)
                        g[i] = a1 + a2
                else:
                    a1 = int(rng.random() < p)
                    a2 = int(rng.random() < p)
                    g[i] = a1 + a2

            genotypes_list.append(g)
            current_idx += 1

    genotypes = np.array(genotypes_list, dtype=int)  # (1200, 120)

    # Impose relatedness:
    # 1. Duplicate pair: SAMPLE_119 (idx 118) and SAMPLE_120 (idx 119)
    genotypes[:, 119] = genotypes[:, 118]

    # 2. Related pair 1 (parent-offspring / full-sib ~50% shared): SAMPLE_010 and SAMPLE_011
    for s_idx in range(len(variants)):
        if rng.random() < 0.50:
            genotypes[s_idx, 10] = genotypes[s_idx, 9]

    # 3. Related pair 2: SAMPLE_050 and SAMPLE_051
    for s_idx in range(len(variants)):
        if rng.random() < 0.50:
            genotypes[s_idx, 50] = genotypes[s_idx, 49]

    # Impose 2% random missingness
    mask_missing = rng.random(genotypes.shape) < 0.02
    genotypes[mask_missing] = -1

    # Write toy_gwas.vcf.gz
    with gzip.open(vcf_path, "wt", encoding="utf-8") as vcf:
        vcf.write("##fileformat=VCFv4.2\n")
        vcf.write("##source=GWAS_Platform_Fixture_Generator\n")
        vcf.write("##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n")
        for c in chroms:
            vcf.write(f"##contig=<ID={c},length=10000000>\n")
        vcf.write("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(sample_ids) + "\n")

        for v_idx, (chrom, pos, snp_id, ref, alt) in enumerate(variants):
            row_gts = []
            for s_idx in range(n_samples):
                val = genotypes[v_idx, s_idx]
                if val == -1:
                    row_gts.append("./.")
                elif val == 0:
                    row_gts.append("0/0")
                elif val == 1:
                    row_gts.append("0/1")
                elif val == 2:
                    row_gts.append("1/1")
                else:
                    row_gts.append("./.")
            vcf.write(f"{chrom}\t{pos}\t{snp_id}\t{ref}\t{alt}\t100\tPASS\t.\tGT\t" + "\t".join(row_gts) + "\n")

    # Generate toy_pheno.txt
    # Phenotype model:
    # y = 10.0 + 2.8 * dosage(causal1) + 2.0 * dosage(causal2) + subpop_effect + noise
    pheno_lines = ["FID\tIID\tTRAIT1\tTRAIT_TEXT"]

    subpop_effects = [-1.5, 0.2, 1.3]
    for s_idx, sample_id in enumerate(sample_ids):
        g1 = genotypes[causal_1_idx, s_idx] if causal_1_idx is not None else 0
        g2 = genotypes[causal_2_idx, s_idx] if causal_2_idx is not None else 0
        d1 = max(g1, 0)
        d2 = max(g2, 0)
        pop_eff = subpop_effects[subpop[s_idx]]
        noise = float(rng.normal(0.0, 1.0))
        val = 10.0 + (2.8 * d1) + (2.0 * d2) + pop_eff + noise
        text_label = "case" if val > 12.0 else "control"

        # Special missing values according to specification:
        # 5 samples NA: samples 5, 15, 25, 35, 45
        if s_idx in {5, 15, 25, 35, 45}:
            pheno_lines.append(f"{sample_id}\t{sample_id}\tNA\t{text_label}")
        # 2 samples -9: samples 65, 75
        elif s_idx in {65, 75}:
            pheno_lines.append(f"{sample_id}\t{sample_id}\t-9\t{text_label}")
        # 1 sample non-numeric value: sample 85
        elif s_idx == 85:
            pheno_lines.append(f"{sample_id}\t{sample_id}\tINVALID_VALUE\t{text_label}")
        else:
            pheno_lines.append(f"{sample_id}\t{sample_id}\t{val:.4f}\t{text_label}")

    # 3 IIDs that do not exist in VCF:
    pheno_lines.append("EXTRA_001\tEXTRA_001\t11.5200\tcontrol")
    pheno_lines.append("EXTRA_002\tEXTRA_002\t14.1200\tcase")
    pheno_lines.append("EXTRA_003\tEXTRA_003\t9.8500\tcontrol")

    # 1 duplicate IID (repeat SAMPLE_001 with slightly different value)
    pheno_lines.append("SAMPLE_001\tSAMPLE_001\t10.5000\tcontrol")

    pheno_path.write_text("\n".join(pheno_lines) + "\n", encoding="utf-8")

    # Generate toy_annotation.gff3
    # Chr prefix tested: Chr1, Chr2, Chr3
    gff_lines = [
        "##gff-version 3",
        "##sequence-region Chr1 1 10000000",
        "##sequence-region Chr2 1 10000000",
        "##sequence-region Chr3 1 10000000",
        # Gene 1: contains causal SNP 1 at 1:2,500,000 (span: 2,490,000 to 2,510,000)
        "Chr1\tSimGWAS\tgene\t2490000\t2510000\t.\t+\t.\tID=GENE_CAUSAL1;Name=GENE_CAUSAL1;Note=Causal gene for trait 1",
        "Chr1\tSimGWAS\tmRNA\t2490000\t2510000\t.\t+\t.\tID=mRNA_CAUSAL1;Parent=GENE_CAUSAL1",
        # Gene 2: ~30 kb away from causal SNP 2 at 2:1,200,000 (span: 1,230,000 to 1,245,000)
        "Chr2\tSimGWAS\tgene\t1230000\t1245000\t.\t+\t.\tID=GENE_NEAR2;Name=GENE_NEAR2;Note=Near causal SNP 2",
        "Chr2\tSimGWAS\tmRNA\t1230000\t1245000\t.\t+\t.\tID=mRNA_NEAR2;Parent=GENE_NEAR2",
        # Gene 3 & 4: background genes on Chr 1 and Chr 3
        "Chr1\tSimGWAS\tgene\t3500000\t3520000\t.\t-\t.\tID=GENE_BG1;Name=GENE_BG1;Note=Background gene 1",
        "Chr3\tSimGWAS\tgene\t1800000\t1825000\t.\t+\t.\tID=GENE_BG3;Name=GENE_BG3;Note=Background gene 3",
    ]
    gff_path.write_text("\n".join(gff_lines) + "\n", encoding="utf-8")

    return vcf_path, pheno_path, gff_path


if __name__ == "__main__":
    v, p, g = ensure_fixtures(force=True)
    print(f"Generated fixtures:\n - VCF: {v}\n - Phenotype: {p}\n - GFF3: {g}")
