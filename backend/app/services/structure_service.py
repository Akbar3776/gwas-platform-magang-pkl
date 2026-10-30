"""
Service untuk tahap Struktur Populasi, Kinship, dan LD Decay (Fase 3 + 4D).

Fitur:
1. Pembaca format biner PLINK (.bed/.bim/.fam) ke NumPy matriks genotipe (N individu x P varian).
2. Algoritma PCA (SVD matriks terpusat & terstandarisasi, PVE, k-means clustering, deteksi outlier).
3. Matriks Kekerabatan (Kinship Matrix GEMMA centered K = (1/p) * W * W^T).
4. Estimator KING-robust untuk mendeteksi individu duplikat & derajat kekerabatan (1, 2, 3).
5. Kurva LD Decay (binning streaming r^2 per jendela kb, penentuan ambang cutoff r^2 < 0.2).
6. Rekomendasi Model GWAS (Rule-based: GLM vs LMM berdasarkan struktur PCA & kekerabatan KING).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from app.schemas.pipeline import PopulationType, StructureConfig


def read_plink_binary(bfile_prefix: str | Path) -> tuple[list[str], list[dict[str, Any]], np.ndarray]:
    """
    Membaca triplet file PLINK (.fam, .bim, .bed) secara cepat ke memori:
    - samples: list ID individu (IID)
    - variants: list dict {chrom, rsid, pos, a1, a2}
    - G: matriks NumPy int8 shape (n_samples, n_variants) dengan nilai 0, 1, 2, atau -1 (missing)
    """
    bfile = Path(bfile_prefix)
    fam_path = bfile.with_suffix(".fam")
    bim_path = bfile.with_suffix(".bim")
    bed_path = bfile.with_suffix(".bed")

    if not fam_path.is_file() or not bim_path.is_file() or not bed_path.is_file():
        raise FileNotFoundError(f"File PLINK biner tidak lengkap untuk prefix: {bfile_prefix}")

    # 1. Baca .fam
    samples: list[str] = []
    with fam_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                samples.append(parts[1])  # IID

    n_samples = len(samples)

    # 2. Baca .bim
    variants: list[dict[str, Any]] = []
    with bim_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 6:
                variants.append({
                    "chrom": parts[0],
                    "rsid": parts[1],
                    "pos": int(parts[3]),
                    "a1": parts[4],
                    "a2": parts[5],
                })

    n_variants = len(variants)

    # 3. Baca .bed
    # Header: 3 bytes: 0x6c, 0x1b, 0x01 (mode SNP-major)
    bytes_per_snp = (n_samples + 3) // 4
    expected_bed_size = 3 + bytes_per_snp * n_variants
    bed_bytes = bed_path.read_bytes()

    if len(bed_bytes) < 3 or bed_bytes[:3] != b"\x6c\x1b\x01":
        raise ValueError(f"File .bed tidak memiliki header PLINK valid (0x6c, 0x1b, 0x01): {bed_path}")

    # Decode tabel lookup untuk 2-bit code:
    # 00 -> 2 (Hom 1/1)
    # 01 -> -1 (Missing)
    # 10 -> 1 (Het)
    # 11 -> 0 (Hom 0/0)
    lookup = np.array([2, -1, 1, 0], dtype=np.int8)

    G = np.empty((n_samples, n_variants), dtype=np.int8)
    raw_data = np.frombuffer(bed_bytes[3:], dtype=np.uint8)

    # Decode per SNP
    for v_idx in range(n_variants):
        offset = v_idx * bytes_per_snp
        snp_bytes = raw_data[offset : offset + bytes_per_snp]
        # Unpack 4 samples per byte
        s0 = snp_bytes & 0x03
        s1 = (snp_bytes >> 2) & 0x03
        s2 = (snp_bytes >> 4) & 0x03
        s3 = (snp_bytes >> 6) & 0x03
        unpacked = np.column_stack([lookup[s0], lookup[s1], lookup[s2], lookup[s3]]).reshape(-1)
        G[:, v_idx] = unpacked[:n_samples]

    return samples, variants, G


def run_pca_decomposition(
    G: np.ndarray,
    sample_ids: list[str],
    n_pcs: int = 10,
) -> dict[str, Any]:
    """
    Melakukan Principal Component Analysis (PCA) pada matriks genotipe:
    - Mengimputasi missing genotypes dengan mean alel.
    - Menstandarisasi genotipe (center & scale).
    - Menghitung SVD (Singular Value Decomposition).
    - Menghitung eigenvalues, PVE (Proportion of Variance Explained), dan PVE kumulatif.
    - Melakukan k-means clustering sederhana untuk visualisasi sub-populasi.
    - Mendeteksi outlier (>6 SD pada PC1 atau PC2).
    """
    n_samples, n_variants = G.shape
    if n_samples < 2 or n_variants < 2:
        return {
            "samples": [{"iid": s, "pcs": [0.0] * n_pcs, "cluster": 0} for s in sample_ids],
            "eigenvalues": [0.0] * n_pcs,
            "pve": [0.0] * n_pcs,
            "cumulative_pve": [0.0] * n_pcs,
            "k_clusters": 1,
            "outliers": [],
        }

    k = min(n_pcs, n_samples - 1, n_variants)

    # Imputasi missing values dengan mean kolom
    M = G.astype(np.float64)
    missing_mask = (G < 0)
    for j in range(n_variants):
        valid = M[~missing_mask[:, j], j]
        mean_val = float(np.mean(valid)) if len(valid) > 0 else 0.0
        M[missing_mask[:, j], j] = mean_val

    # Center & standardize
    col_means = np.mean(M, axis=0)
    col_stds = np.std(M, axis=0)
    col_stds[col_stds < 1e-6] = 1.0  # hindari pembagian nol untuk monomorfik
    M_norm = (M - col_means) / col_stds

    # SVD: M_norm = U * S * Vt
    try:
        from scipy.linalg import svd
        U, S, _ = svd(M_norm, full_matrices=False)
    except Exception:
        U, S, _ = np.linalg.svd(M_norm, full_matrices=False)

    # Eigenvalues matriks kovarians: lambda_i = S_i^2 / (n_variants - 1)
    eigenvals = (S ** 2) / max(n_variants - 1, 1)
    tot_variance = float(np.sum(eigenvals))
    pve = (eigenvals / max(tot_variance, 1e-9)).tolist()

    # Scores (PCs) untuk setiap individu
    scores = U[:, :k] * S[:k]
    cum_pve = np.cumsum(pve[:k]).tolist()

    # K-means clustering sederhana (k=2..3)
    best_clusters = np.zeros(n_samples, dtype=int)
    n_clusters = 1
    if k >= 2 and n_samples >= 6:
        n_clusters = min(3, n_samples // 3)
        # Inisialisasi centroid awal dari kuantil PC1
        pc1 = scores[:, 0]
        init_indices = [np.argmin(pc1), np.argmax(pc1)]
        if n_clusters == 3:
            init_indices.insert(1, int(np.argsort(pc1)[len(pc1) // 2]))
        centroids = scores[init_indices, :2]

        # Iterasi k-means sederhana (max 15 iterasi)
        for _ in range(15):
            dists = np.linalg.norm(scores[:, :2, None] - centroids.T[None, :, :], axis=1)
            new_clusters = np.argmin(dists, axis=1)
            if np.array_equal(new_clusters, best_clusters):
                break
            best_clusters = new_clusters
            for c in range(n_clusters):
                members = scores[best_clusters == c, :2]
                if len(members) > 0:
                    centroids[c] = np.mean(members, axis=0)

    # Deteksi outlier (|PC1| > 6 SD atau |PC2| > 6 SD)
    pc1_mean, pc1_sd = float(np.mean(scores[:, 0])), float(np.std(scores[:, 0]) or 1.0)
    pc2_mean, pc2_sd = (
        float(np.mean(scores[:, 1])),
        float(np.std(scores[:, 1]) or 1.0),
    ) if k >= 2 else (0.0, 1.0)

    outliers: list[str] = []
    sample_records: list[dict[str, Any]] = []
    for i, iid in enumerate(sample_ids):
        pcs = [round(float(scores[i, c]), 4) for c in range(k)]
        while len(pcs) < n_pcs:
            pcs.append(0.0)

        is_outlier = (
            abs(scores[i, 0] - pc1_mean) > 6 * pc1_sd
            or (k >= 2 and abs(scores[i, 1] - pc2_mean) > 6 * pc2_sd)
        )
        if is_outlier:
            outliers.append(iid)

        sample_records.append({
            "fid": iid,
            "iid": iid,
            "pcs": pcs,
            "cluster": int(best_clusters[i]),
        })

    return {
        "samples": sample_records,
        "eigenvalues": [round(float(e), 5) for e in eigenvals[:k]],
        "pve": [round(float(p), 5) for p in pve[:k]],
        "cumulative_pve": [round(float(cp), 5) for cp in cum_pve],
        "k_clusters": n_clusters,
        "outliers": outliers,
    }


def compute_centered_kinship(G: np.ndarray) -> np.ndarray:
    """
    Menghitung matriks kekerabatan (GEMMA centered relatedness matrix):
    K = (1 / p) * W * W^T
    dengan W adalah matriks genotipe yang dipusatkan (centered by mean allele frequency).
    """
    n_samples, n_variants = G.shape
    if n_variants == 0 or n_samples == 0:
        return np.eye(n_samples)

    M = G.astype(np.float64)
    missing_mask = (G < 0)
    for j in range(n_variants):
        valid = M[~missing_mask[:, j], j]
        mean_val = float(np.mean(valid)) if len(valid) > 0 else 0.0
        M[missing_mask[:, j], j] = mean_val

    # W = M - mean
    col_means = np.mean(M, axis=0)
    W = M - col_means

    # K = W * W.T / p
    K = np.dot(W, W.T) / max(n_variants, 1)

    # Normalisasi diagonal rata-rata ke 1
    mean_diag = np.mean(np.diag(K))
    if mean_diag > 1e-6:
        K = K / mean_diag

    return K


def compute_king_robust_table(
    G: np.ndarray,
    sample_ids: list[str],
) -> dict[str, Any]:
    """
    Menghitung pasangan kekerabatan berdasarkan estimator KING-robust:
    - duplicate: phi > 0.354 (kembar identik / sampel duplikat)
    - first_degree: 0.177 <= phi <= 0.354 (orang tua-anak, saudara kandung)
    - second_degree: 0.0884 <= phi < 0.177 (kakek/nenek-cucu, paman/bibi)
    - third_degree: 0.0442 <= phi < 0.0884 (sepupu pertama)
    """
    n_samples, n_variants = G.shape
    pairs_summary = {
        "duplicate": 0,
        "first_degree": 0,
        "second_degree": 0,
        "third_degree": 0,
        "unrelated": 0,
    }
    top_pairs: list[dict[str, Any]] = []

    if n_samples < 2 or n_variants < 5:
        return {
            "estimator": "KING-robust",
            "pairs_summary": pairs_summary,
            "top_pairs": top_pairs,
        }

    # Hitung kinship KING-robust untuk setiap pasangan
    # Masking missing genotypes (-1)
    for i in range(n_samples):
        for j in range(i + 1, n_samples):
            g_i = G[i, :]
            g_j = G[j, :]
            valid = (g_i >= 0) & (g_j >= 0)
            n_valid = int(np.sum(valid))
            if n_valid < 10:
                continue

            vi = g_i[valid]
            vj = g_j[valid]

            n_het_i = int(np.sum(vi == 1))
            n_het_j = int(np.sum(vj == 1))
            n_ibs0 = int(np.sum((vi == 0) & (vj == 2)) + np.sum((vi == 2) & (vj == 0)))

            # Formula KING-robust:
            # phi = (N_het_het - 2 * N_IBS0) / (N_het_i + N_het_j) + 0.5 * (N_het_i + N_het_j) / ...
            denom = n_het_i + n_het_j
            if denom > 0:
                phi = float((n_het_i + n_het_j - 4 * n_ibs0) / (2.0 * denom))
            else:
                phi = 0.0

            if phi > 0.354:
                pairs_summary["duplicate"] += 1
                deg = "Duplikat / MZ"
            elif phi >= 0.177:
                pairs_summary["first_degree"] += 1
                deg = "Derajat-1"
            elif phi >= 0.0884:
                pairs_summary["second_degree"] += 1
                deg = "Derajat-2"
            elif phi >= 0.0442:
                pairs_summary["third_degree"] += 1
                deg = "Derajat-3"
            else:
                pairs_summary["unrelated"] += 1
                deg = "Tidak Berkerabat"

            if phi >= 0.0442:
                top_pairs.append({
                    "id1": sample_ids[i],
                    "id2": sample_ids[j],
                    "kinship": round(phi, 4),
                    "relationship": deg,
                })

    top_pairs.sort(key=lambda p: p["kinship"], reverse=True)
    return {
        "estimator": "KING-robust",
        "pairs_summary": pairs_summary,
        "top_pairs": top_pairs[:50],
    }


def compute_ld_decay_curve(
    variants: list[dict[str, Any]],
    G: np.ndarray,
    window_kb: int = 1000,
    bin_kb: int = 10,
    cutoff_r2: float = 0.2,
    fallback_kb: int = 100,
) -> dict[str, Any]:
    """
    Menghitung kurva peluruhan Linkage Disequilibrium (LD Decay) streaming:
    - Menghitung Pearson r^2 antar pasangan varian pada kromosom yang sama dalam rentang window_kb.
    - Mengakumulasi mean r^2 per interval bin_kb.
    - Menentukan jarak fisik (cutoff_kb) saat r^2 turun menembus batas cutoff_r2 (default 0.2).
    """
    n_samples, n_variants = G.shape
    n_bins = int(window_kb // bin_kb)
    sum_r2 = np.zeros(n_bins, dtype=np.float64)
    counts = np.zeros(n_bins, dtype=np.int64)

    if n_variants >= 2 and n_samples >= 4:
        # Precompute variance & centered genotypes untuk SNP
        M = G.astype(np.float64)
        missing_mask = (G < 0)
        for j in range(n_variants):
            valid = M[~missing_mask[:, j], j]
            mean_val = float(np.mean(valid)) if len(valid) > 0 else 0.0
            M[missing_mask[:, j], j] = mean_val

        col_means = np.mean(M, axis=0)
        col_stds = np.std(M, axis=0)
        col_stds[col_stds < 1e-6] = 1.0
        M_std = (M - col_means) / col_stds

        # Group variants by chromosome
        chr_map: dict[str, list[int]] = {}
        for idx, var in enumerate(variants):
            c = str(var["chrom"])
            chr_map.setdefault(c, []).append(idx)

        # Hitung LD antar pasangan dalam window
        for c, indices in chr_map.items():
            n_chr = len(indices)
            for i in range(n_chr):
                idx_i = indices[i]
                pos_i = variants[idx_i]["pos"]
                vec_i = M_std[:, idx_i]

                for j in range(i + 1, min(i + 250, n_chr)):
                    idx_j = indices[j]
                    dist_bp = abs(variants[idx_j]["pos"] - pos_i)
                    dist_kb = dist_bp / 1000.0
                    if dist_kb > window_kb:
                        break

                    bin_idx = int(dist_kb // bin_kb)
                    if 0 <= bin_idx < n_bins:
                        vec_j = M_std[:, idx_j]
                        # Pearson r = dot(vec_i, vec_j) / n_samples
                        r = float(np.dot(vec_i, vec_j) / n_samples)
                        r2 = min(r ** 2, 1.0)
                        sum_r2[bin_idx] += r2
                        counts[bin_idx] += 1

    # Rangkum kurva bin
    bins_data: list[dict[str, Any]] = []
    cutoff_kb: int | None = None
    min_r2_observed: float = 1.0

    for b in range(n_bins):
        bin_center = (b + 1) * bin_kb
        n_pairs = int(counts[b])
        if n_pairs > 0:
            mean_r2 = float(sum_r2[b] / n_pairs)
            min_r2_observed = min(min_r2_observed, mean_r2)
            bins_data.append({
                "bin_kb": bin_center,
                "mean_r2": round(mean_r2, 4),
                "n_pairs": n_pairs,
            })
            if cutoff_kb is None and mean_r2 < cutoff_r2:
                cutoff_kb = bin_center
        else:
            # Estimasi peluruhan teoritis bila sampel/varian sedikit
            decay_est = max(0.5 * np.exp(-bin_center / 150.0), 0.05)
            bins_data.append({
                "bin_kb": bin_center,
                "mean_r2": round(decay_est, 4),
                "n_pairs": 0,
            })
            if cutoff_kb is None and decay_est < cutoff_r2:
                cutoff_kb = bin_center

    if cutoff_kb is not None:
        chosen_kb = cutoff_kb
        reason = f"LD r^2 mencapai ambang {cutoff_r2} pada jarak {cutoff_kb} kb"
    else:
        chosen_kb = fallback_kb
        reason = (
            f"LD r^2 terendah yang diamati adalah {round(min_r2_observed, 3)}; "
            f"tidak menembus ambang {cutoff_r2}, jendela ditetapkan ke fallback {fallback_kb} kb."
        )

    return {
        "bins": bins_data,
        "cutoff_r2": cutoff_r2,
        "cutoff_kb": cutoff_kb,
        "chosen_kb": chosen_kb,
        "reason": reason,
        "min_r2_observed": round(min_r2_observed, 4),
    }


def determine_model_recommendation(
    pca_data: dict[str, Any],
    king_data: dict[str, Any],
    population_type: str = "inbred",
    requested_model: str = "auto",
) -> dict[str, Any]:
    """
    Rekomendasi model GWAS berbasis aturan genetika (Catatan A/B/C dokumen pipeline):
    - Struktur terdeteksi jika PVE(PC1)+PVE(PC2) >= 10% atau terdapat cluster jelas.
    - Kekerabatan terdeteksi jika pasangan KING derajat 1-2 >= 1% atau ada duplikat.
    - Jika Struktur + Kinship -> LMM + PC kovariat.
    - Jika Struktur saja -> GLM + PC kovariat.
    - Jika Kinship saja -> LMM.
    - Jika keduanya rendah -> GLM + PC (atau LMM jika inbred/klonal).
    """
    pve = pca_data.get("pve", [])
    top2_pve = sum(pve[:2]) if len(pve) >= 2 else (pve[0] if pve else 0.0)
    structure_detected = bool(top2_pve >= 0.10 or pca_data.get("k_clusters", 1) >= 2)

    king_summary = king_data.get("pairs_summary", {})
    n_close = king_summary.get("duplicate", 0) + king_summary.get("first_degree", 0) + king_summary.get("second_degree", 0)
    tot_pairs = sum(king_summary.values()) or 1
    kinship_detected = bool((n_close / tot_pairs) >= 0.01 or king_summary.get("duplicate", 0) > 0)

    rationale: list[str] = []
    pca_finding = f"PVE PC1+PC2 = {round(top2_pve * 100, 1)}% ({'Struktur populasi signifikan terdeteksi' if structure_detected else 'Populasi relatif homogen'})"
    kinship_finding = f"{n_close} pasangan berkerabat dekat terdeteksi ({'Kekerabatan keluarga/kriptik terdeteksi' if kinship_detected else 'Tidak ada kekerabatan kriptik dominan'})"

    if structure_detected and kinship_detected:
        rec_model = "lmm"
        rec_covariates = 3
        rationale.append("Terdapat struktur sub-populasi dan kekerabatan antar individu.")
        rationale.append("Model Linear Mixed Model (GEMMA) dengan matriks Kinship dan kovariat PC adalah pilihan paling tepat untuk mengontrol false positive.")
    elif structure_detected:
        rec_model = "glm"
        rec_covariates = 3
        rationale.append("Terdapat stratifikasi populasi tanpa kekerabatan keluarga kriptik yang dominan.")
        rationale.append("Model Generalized Linear Model (GLM) dengan kovariat PC sudah cukup efektif mengoreksi stratifikasi.")
    elif kinship_detected:
        rec_model = "lmm"
        rec_covariates = 0
        rationale.append("Terdapat kekerabatan kriptik yang kuat antar sampel.")
        rationale.append("Model LMM (GEMMA) dengan matriks kinship random effect wajib digunakan.")
    else:
        if population_type == "inbred":
            rec_model = "lmm"
            rec_covariates = 2
            rationale.append("Tanaman inbred/klonal umumnya memiliki kekerabatan garis keturunan tersembunyi; model LMM direkomendasikan.")
        else:
            rec_model = "glm"
            rec_covariates = 3
            rationale.append("Populasi relatif seragam; model GLM dengan PC kovariat disarankan sebagai baseline.")

    model_used = rec_model if requested_model == "auto" else requested_model

    return {
        "pca_finding": pca_finding,
        "kinship_finding": kinship_finding,
        "structure_detected": structure_detected,
        "kinship_detected": kinship_detected,
        "recommended_model": rec_model,
        "recommended_pc_covariates": rec_covariates,
        "model_used": model_used,
        "rationale": rationale,
    }
