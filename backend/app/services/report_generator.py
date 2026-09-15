"""
services/report_generator.py

Logika pembuatan PDF ringkasan hasil GWAS untuk keperluan publikasi/dokumentasi
peneliti. Berisi: parameter QC (mis. threshold MAF), jumlah sampel lolos/gagal,
dan Top 10 SNP paling signifikan.

Pilihan tool: WeasyPrint + Jinja2.
Alasan: laporan ini adalah dokumen dengan layout tabel & styling yang perlu rapi
untuk lampiran jurnal — menulis HTML/CSS lalu merender ke PDF jauh lebih mudah
dipelihara (dan lebih mudah didesain ulang oleh non-engineer) dibanding menyusun
tabel secara imperatif lewat ReportLab. Trade-off: WeasyPrint butuh dependency
sistem (Pango/Cairo) — pastikan image Docker backend menginstalnya.

Prinsip performa penting:
- File `.assoc.txt` bisa berisi jutaan baris SNP. `extract_top_snps` membaca file
  BARIS PER BARIS (bukan `pandas.read_csv` tanpa chunksize, bukan `readlines()`)
  dan hanya menyimpan `top_n` kandidat sekaligus lewat bounded max-heap — memory
  footprint konstan (~top_n baris) berapa pun ukuran file aslinya.
"""

import heapq
from datetime import datetime
from pathlib import Path
from typing import Optional, TypedDict

import jinja2
from weasyprint import HTML

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

# Kolom p-value berbeda nama antar tool: PLINK --assoc pakai "P", GEMMA pakai "p_wald".
COLUMN_ALIASES = {
    "chr": ["CHR", "chr"],
    "snp": ["SNP", "rs"],
    "bp": ["BP", "ps"],
    "p_value": ["P", "p_wald", "p_lrt", "p_score"],
}


class SnpRow(TypedDict):
    chr: str
    snp: str
    bp: str
    p_value: float


def _resolve_column_indices(header_cols: list[str]) -> dict[str, int]:
    indices: dict[str, int] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in header_cols:
                indices[field] = header_cols.index(alias)
                break
        if field not in indices:
            raise ValueError(
                f"Kolom untuk '{field}' tidak ditemukan di header file .assoc "
                f"(dicoba: {aliases}). Header aktual: {header_cols}"
            )
    return indices


def extract_top_snps(assoc_file_path: str, top_n: int = 10) -> list[SnpRow]:
    """
    Streaming top-N SNP dengan p-value terkecil (paling signifikan) dari file
    .assoc / .assoc.txt yang berpotensi sangat besar, tanpa memuat seluruh file
    ke memori sekaligus.
    """
    heap: list[tuple[float, SnpRow]] = []

    with open(assoc_file_path, "r") as f:
        header_line = f.readline()
        header_cols = header_line.split()
        idx = _resolve_column_indices(header_cols)

        for line in f:
            parts = line.split()
            if len(parts) <= max(idx.values()):
                continue  # baris rusak/kependekan, lewati

            raw_p = parts[idx["p_value"]]
            if raw_p in ("NA", "nan", ""):
                continue
            try:
                p_value = float(raw_p)
            except ValueError:
                continue

            row: SnpRow = {
                "chr": parts[idx["chr"]],
                "snp": parts[idx["snp"]],
                "bp": parts[idx["bp"]],
                "p_value": p_value,
            }

            # Bounded max-heap terhadap -p_value == bounded min-heap terhadap p_value.
            # Heap tidak pernah tumbuh lebih besar dari top_n elemen.
            if len(heap) < top_n:
                heapq.heappush(heap, (-p_value, row))
            elif -p_value > heap[0][0]:
                heapq.heapreplace(heap, (-p_value, row))

    return sorted((item[1] for item in heap), key=lambda r: r["p_value"])


def build_report_context(
    job,
    job_steps: list,
    qc_summary: dict,
    top_snps: list[SnpRow],
) -> dict:
    """Susun semua data yang dibutuhkan template HTML menjadi satu dict."""
    return {
        "job_id": str(job.id),
        "generated_at": datetime.now().strftime("%d %B %Y, %H:%M"),
        "pipeline_type": job.pipeline_type,
        "parameters": job.parameters or {},
        "qc_summary": qc_summary,
        "top_snps": top_snps,
        "steps": job_steps,
    }


def render_pdf_report(context: dict) -> bytes:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template("report_template.html")
    html_content = template.render(**context)
    return HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf()


def parse_qc_summary_from_log(log_excerpt: Optional[str]) -> dict:
    """
    Parsing ringan terhadap `log_excerpt` PLINK QC untuk mengambil jumlah sampel
    lolos/gagal filter.

    CATATAN DESAIN: ini adalah fallback. Cara yang lebih tahan-banting adalah
    menyimpan angka ini secara terstruktur ke `results.summary_stats` (JSONB)
    langsung saat Celery task QC selesai (lihat skema `RESULTS` di design.md),
    sehingga endpoint report tidak bergantung pada regex terhadap teks log yang
    formatnya bisa berubah antar versi PLINK.
    """
    import re

    if not log_excerpt:
        return {"samples_total": None, "samples_passed": None, "samples_failed": None}

    passed_match = re.search(r"(\d+)\s+people pass filters", log_excerpt)
    total_match = re.search(r"(\d+)\s+people \(", log_excerpt)

    total = int(total_match.group(1)) if total_match else None
    passed = int(passed_match.group(1)) if passed_match else None

    return {
        "samples_total": total,
        "samples_passed": passed,
        "samples_failed": (total - passed) if (total is not None and passed is not None) else None,
    }
