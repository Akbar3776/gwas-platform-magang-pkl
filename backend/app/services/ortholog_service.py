"""Parser, pengindeks, dan pemuat indeks best-hit ortolog BLAST outfmt6."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import gzip
import hashlib
import math
import os
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.gene_id_utils import GENE_ID_RE, strip_isoform_suffix
from app.services.upload_validation import UploadValidationError, open_lines_bounded

_INDEX_HEADER = (
    "query_id\tsubject_locus\tsubject_id\tpident\tevalue\tbitscore\tn_hits\tn_loci_at_best\n"
)


@dataclass(frozen=True, slots=True)
class OrthologHit:
    """Rekaman best-hit ortolog/homolog BLAST untuk satu gen query."""

    subject_locus: str
    subject_id: str
    pident: float
    evalue: float
    bitscore: float
    n_hits: int
    n_loci_at_best: int


@dataclass
class OrthologLookup:
    """Indeks pencarian cepat ortolog dengan aturan exact lalu fallback isoform (C3/C9)."""

    exact: dict[str, OrthologHit]
    base: dict[str, OrthologHit]

    def lookup(self, gene_id: Any) -> OrthologHit | None:
        k = str(gene_id or "").strip().lower()
        if not k:
            return None
        if k in self.exact:
            return self.exact[k]
        if k in self.base:
            return self.base[k]
        base_k = strip_isoform_suffix(k)
        if base_k != k:
            if base_k in self.exact:
                return self.exact[base_k]
            if base_k in self.base:
                return self.base[base_k]
        return None


def _hit_sort_key(hit: OrthologHit) -> tuple[float, float, float, str, str]:
    return (-hit.bitscore, hit.evalue, -hit.pident, hit.subject_locus, hit.subject_id)


def build_ortholog_lookup(index: dict[str, OrthologHit]) -> OrthologLookup:
    """Membangun OrthologLookup (exact dan level gen tanpa sufiks isoform) dari indeks best-hit."""
    exact: dict[str, OrthologHit] = {}
    base: dict[str, OrthologHit] = {}
    for qid, hit in index.items():
        norm = str(qid or "").strip().lower()
        if not norm:
            continue
        cur_exact = exact.get(norm)
        if cur_exact is None or _hit_sort_key(hit) < _hit_sort_key(cur_exact):
            exact[norm] = hit
        base_key = strip_isoform_suffix(norm)
        cur_base = base.get(base_key)
        if cur_base is None or _hit_sort_key(hit) < _hit_sort_key(cur_base):
            base[base_key] = hit
    return OrthologLookup(exact=exact, base=base)


def resolve_dataset_ortholog_index_path(dataset: Any) -> Path | None:
    """Menemukan path indeks best-hit ortolog dataset (metadata lalu file kanonik storage)."""
    if dataset is None:
        return None
    meta = getattr(dataset, "extra_metadata", None) or {}
    orth_meta = meta.get("ortholog") if isinstance(meta, dict) else None
    if isinstance(orth_meta, dict) and orth_meta.get("path"):
        p = Path(orth_meta["path"])
        if p.is_file():
            return p
    ds_id = getattr(dataset, "id", None)
    if ds_id is not None:
        cand = Path(settings.STORAGE_ROOT) / "datasets" / str(ds_id) / "ortholog_besthit.tsv.gz"
        if cand.is_file():
            return cand
    return None


def _is_finite_float(raw: str) -> float | None:
    try:
        val = float(raw)
    except ValueError:
        return None
    return val if math.isfinite(val) else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def build_besthit_index(src_path: Path | str, dest_path: Path | str) -> dict[str, Any]:
    """Mem-parsing BLAST outfmt6 secara aman dan menulis indeks best-hit terkompresi gzip."""
    src = Path(src_path)
    dest = Path(dest_path)

    best: dict[str, tuple[float, float, float, str, str]] = {}
    loci_at_best: dict[str, set[str]] = {}
    n_hits: dict[str, int] = {}
    n_rows = 0
    n_invalid_lines = 0
    checked_first_header = False
    line_stats: dict[str, int] = {"decompressed_bytes": 0, "lines": 0}
    gene_id_match = GENE_ID_RE.match
    max_queries = settings.ORTHOLOG_MAX_QUERIES

    with open_lines_bounded(
        src,
        max_decompressed_bytes=settings.ORTHOLOG_MAX_DECOMPRESSED_BYTES,
        max_lines=settings.ORTHOLOG_MAX_LINES,
        max_line_bytes=settings.UPLOAD_TEXT_MAX_LINE_BYTES,
        stats=line_stats,
    ) as lines:
        for _, line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            cols = stripped.split("\t")
            if not checked_first_header:
                checked_first_header = True
                # Baris pertama non-komentar dengan kolom ke-3 non-numerik dianggap header (F15)
                if len(cols) >= 3 and _is_finite_float(cols[2]) is None:
                    continue

            if len(cols) < 12:
                n_invalid_lines += 1
                continue

            qseqid = cols[0].strip()
            sseqid = cols[1].strip()
            if not (gene_id_match(qseqid) and gene_id_match(sseqid)):
                n_invalid_lines += 1
                continue

            pident = _is_finite_float(cols[2])
            evalue = _is_finite_float(cols[10])
            bitscore = _is_finite_float(cols[11])
            if (
                pident is None
                or evalue is None
                or bitscore is None
                or not (0.0 <= pident <= 100.0)
                or evalue < 0.0
                or bitscore < 0.0
            ):
                n_invalid_lines += 1
                continue

            n_rows += 1
            subject_locus = strip_isoform_suffix(sseqid)
            key = (-bitscore, evalue, -pident, subject_locus, sseqid)
            cur = best.get(qseqid)
            if cur is None:
                if len(best) >= max_queries:
                    raise UploadValidationError(
                        "TOO_MANY_QUERIES",
                        "Jumlah query gen unik pada berkas BLAST melebihi batas maksimal.",
                        http_status=413,
                    )
                best[qseqid] = key
                loci_at_best[qseqid] = {subject_locus}
                n_hits[qseqid] = 1
            else:
                n_hits[qseqid] += 1
                best_bs = -cur[0]
                if bitscore > best_bs:
                    loci_at_best[qseqid] = {subject_locus}
                elif bitscore == best_bs:
                    loci_at_best[qseqid].add(subject_locus)
                if key < cur:
                    best[qseqid] = key

    if n_rows == 0:
        raise UploadValidationError(
            "NO_VALID_ROWS",
            "Berkas BLAST tidak memuat satu pun baris valid outfmt6 (12 kolom).",
            http_status=400,
        )

    total_data_lines = n_rows + n_invalid_lines
    if total_data_lines > 0 and (n_rows / total_data_lines) < settings.GO_MAPPING_MIN_VALID_RATIO:
        raise UploadValidationError(
            "TOO_MANY_INVALID_LINES",
            "Terlalu banyak baris tidak valid pada berkas BLAST yang diunggah.",
            http_status=400,
        )

    n_ties = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_name(f"{dest.name}.part.{os.getpid()}")
    try:
        with gzip.open(tmp_path, "wt", encoding="utf-8", compresslevel=6) as out:
            out.write(_INDEX_HEADER)
            for query_id in sorted(best):
                neg_bs, ev, neg_pid, locus, sseqid = best[query_id]
                n_loci = len(loci_at_best[query_id])
                if n_loci >= 2:
                    n_ties += 1
                out.write(
                    f"{query_id}\t{locus}\t{sseqid}\t{-neg_pid}\t{ev}\t{-neg_bs}\t"
                    f"{n_hits[query_id]}\t{n_loci}\n"
                )
        index_sha256 = _file_sha256(tmp_path)
        os.replace(tmp_path, dest)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return {
        "n_rows": n_rows,
        "n_invalid_lines": n_invalid_lines,
        "n_queries": len(best),
        "n_ties": n_ties,
        "decompressed_bytes": line_stats["decompressed_bytes"],
        "sha256": index_sha256,
    }


@lru_cache(maxsize=4)
def _load_besthit_index_cached(
    resolved_path: str, mtime_ns: int, size: int
) -> dict[str, OrthologHit]:
    del mtime_ns, size
    index: dict[str, OrthologHit] = {}
    with open_lines_bounded(
        Path(resolved_path),
        max_decompressed_bytes=settings.ORTHOLOG_MAX_DECOMPRESSED_BYTES,
        max_lines=settings.ORTHOLOG_MAX_LINES,
        max_line_bytes=settings.UPLOAD_TEXT_MAX_LINE_BYTES,
    ) as lines:
        for line_no, line in lines:
            if line_no == 1 and line.startswith("query_id\t"):
                continue
            stripped = line.strip()
            if not stripped:
                continue
            cols = stripped.split("\t")
            if len(cols) < 8:
                continue
            index[cols[0]] = OrthologHit(
                subject_locus=cols[1],
                subject_id=cols[2],
                pident=float(cols[3]),
                evalue=float(cols[4]),
                bitscore=float(cols[5]),
                n_hits=int(cols[6]),
                n_loci_at_best=int(cols[7]),
            )
    return index


def load_besthit_index(path: Path | str) -> dict[str, OrthologHit]:
    """Memuat indeks best-hit ortolog dengan cache berbasis path, mtime, dan ukuran file."""
    resolved = Path(path).resolve()
    stat = resolved.stat()
    return _load_besthit_index_cached(str(resolved), stat.st_mtime_ns, stat.st_size)


def clear_ortholog_cache() -> None:
    """Mengosongkan cache indeks best-hit ortolog (dipakai saat pengujian)."""
    _load_besthit_index_cached.cache_clear()
