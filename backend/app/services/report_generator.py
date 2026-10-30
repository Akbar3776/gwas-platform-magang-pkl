"""
services/report_generator.py

Logika pembuatan PDF ringkasan hasil GWAS untuk keperluan publikasi/dokumentasi
peneliti. Berisi: parameter QC (mis. threshold MAF), jumlah sampel lolos/gagal,
dan Top 10 SNP paling signifikan.
"""

import heapq
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, TypedDict

import jinja2

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
    heap: list[tuple[float, int, SnpRow]] = []

    with open(assoc_file_path, "r", encoding="utf-8", errors="replace") as f:
        header_line = f.readline()
        header_cols = header_line.split()
        idx = _resolve_column_indices(header_cols)

        for line_idx, line in enumerate(f):
            parts = line.split()
            if len(parts) <= max(idx.values()):
                continue

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

            if len(heap) < top_n:
                heapq.heappush(heap, (-p_value, line_idx, row))
            elif -p_value > heap[0][0]:
                heapq.heapreplace(heap, (-p_value, line_idx, row))

    return sorted((item[2] for item in heap), key=lambda r: r["p_value"])


def build_report_context(
    job,
    job_steps: list,
    qc_summary: dict,
    top_snps: list[SnpRow],
) -> dict:
    """Susun semua data yang dibutuhkan template HTML menjadi satu dict."""
    raw_params = job.parameters or {}
    # Filter out internal file path keys so the PDF parameter table stays clean for researchers
    display_params = {
        k: v
        for k, v in raw_params.items()
        if k not in {"manhattan_cache_path", "qc_log_path", "summary_stats", "qc_summary"}
    }
    return {
        "job_id": str(job.id),
        "generated_at": datetime.now().strftime("%d %B %Y, %H:%M"),
        "pipeline_type": job.pipeline_type,
        "parameters": display_params,
        "qc_summary": qc_summary,
        "top_snps": top_snps,
        "steps": job_steps,
    }


def _build_fallback_pdf(context: dict) -> bytes:
    """Generate a valid PDF document without native GTK/Pango dependencies when WeasyPrint is unavailable."""
    lines = [
        "LAPORAN RINGKASAN ANALISIS GWAS",
        f"Job ID: {context.get('job_id', '-')}",
        f"Tanggal: {context.get('generated_at', '-')} | Tipe Pipeline: {context.get('pipeline_type', '-')}",
        "",
        "1. RINGKASAN QUALITY CONTROL (QC)",
    ]
    qc = context.get("qc_summary") or {}
    lines.append(
        f"   Total Sampel: {qc.get('samples_total', '-')} | "
        f"Lolos Filter: {qc.get('samples_passed', '-')} | "
        f"Gagal Filter: {qc.get('samples_failed', '-')}"
    )
    lines.append("")
    lines.append("2. TOP 10 SNP PALING SIGNIFIKAN")
    lines.append("   No   CHR   SNP ID            BP            P-VALUE")
    for idx, snp in enumerate(context.get("top_snps") or [], start=1):
        lines.append(
            f"   {idx:<4} {str(snp['chr']):<5} {str(snp['snp']):<17} {str(snp['bp']):<13} {snp['p_value']:.3e}"
        )

    def _escape_pdf_text(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_ops = ["BT", "/F1 10 Tf", "50 780 Td", "14 TL"]
    for line in lines[:45]:
        content_ops.append(f"({_escape_pdf_text(line)}) Tj T*")
    content_ops.append("ET")
    stream_bytes = "\n".join(content_ops).encode("latin-1", errors="replace")

    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n",
        b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n",
        f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("ascii")
        + stream_bytes
        + b"\nendstream\nendobj\n",
    ]

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for obj in objs:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_pos = len(pdf)
    pdf.extend(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets:
        pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def render_pdf_report(context: dict) -> bytes:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template("report_template.html")
    html_content = template.render(**context)
    try:
        from weasyprint import HTML
        return HTML(string=html_content, base_url=str(TEMPLATE_DIR)).write_pdf()
    except Exception:
        return _build_fallback_pdf(context)


def parse_qc_summary_from_log(log_excerpt: Optional[str]) -> dict:
    """
    Parsing ringan terhadap `log_excerpt` PLINK QC untuk mengambil jumlah sampel
    lolos/gagal filter.
    """
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
