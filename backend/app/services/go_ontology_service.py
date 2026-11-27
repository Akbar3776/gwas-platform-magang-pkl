"""Parser dan resolver ontologi Gene Ontology (go-basic.obo) lokal dengan cache in-process."""

from __future__ import annotations

from dataclasses import dataclass
import functools
import hashlib
import logging
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping

from app.core.config import settings
from app.services.upload_validation import open_lines_bounded

logger = logging.getLogger(__name__)

_NAMESPACE_TO_ASPECT: dict[str, Literal["BP", "CC", "MF"]] = {
    "biological_process": "BP",
    "cellular_component": "CC",
    "molecular_function": "MF",
}


@dataclass(frozen=True)
class GoTerm:
    id: str
    name: str
    namespace: str
    aspect: Literal["BP", "CC", "MF"] | None
    is_obsolete: bool
    replaced_by: tuple[str, ...]
    consider: tuple[str, ...]
    alt_ids: tuple[str, ...]


@dataclass(frozen=True)
class GoResolution:
    input_id: str
    final_id: str | None
    aspect: str | None
    name: str | None
    status: Literal["ok", "alt_remapped", "obsolete_remapped", "obsolete_dropped", "unknown"]
    via_alt_id: bool


class GoOntology:
    """Representasi read-only graf istilah GO beserta aturan resolusi alt_id dan obsolete."""

    def __init__(
        self,
        *,
        version: str,
        sha256: str,
        terms: dict[str, GoTerm],
        alt_to_primary: dict[str, str],
    ) -> None:
        self.version = version
        self.sha256 = sha256
        self.terms: Mapping[str, GoTerm] = MappingProxyType(terms)
        self.alt_to_primary: Mapping[str, str] = MappingProxyType(alt_to_primary)

    def resolve(self, go_id: str) -> GoResolution:
        cur = str(go_id or "").strip().upper()
        via_alt = False
        visited: set[str] = set()
        hops = 0
        remapped = False

        if cur not in self.terms and cur in self.alt_to_primary:
            cur = self.alt_to_primary[cur]
            via_alt = True

        while True:
            term = self.terms.get(cur)
            if term is None:
                return GoResolution(
                    input_id=go_id,
                    final_id=cur,
                    aspect=None,
                    name=None,
                    status="unknown",
                    via_alt_id=via_alt,
                )
            if not term.is_obsolete:
                if remapped:
                    res_status: Literal["ok", "alt_remapped", "obsolete_remapped"] = "obsolete_remapped"
                elif via_alt:
                    res_status = "alt_remapped"
                else:
                    res_status = "ok"
                return GoResolution(
                    input_id=go_id,
                    final_id=cur,
                    aspect=term.aspect,
                    name=term.name,
                    status=res_status,
                    via_alt_id=via_alt,
                )
            if len(term.replaced_by) == 1 and cur not in visited and hops < 10:
                visited.add(cur)
                nxt = term.replaced_by[0]
                cur = self.alt_to_primary.get(nxt, nxt)
                hops += 1
                remapped = True
                continue
            return GoResolution(
                input_id=go_id,
                final_id=None,
                aspect=None,
                name=None,
                status="obsolete_dropped",
                via_alt_id=via_alt,
            )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@functools.lru_cache(maxsize=2)
def _load_go_ontology_cached(
    resolved_path: str,
    st_mtime_ns: int,
    st_size: int,
    max_bytes: int,
    max_lines: int,
    max_line_bytes: int,
) -> GoOntology:
    path = Path(resolved_path)
    sha256_hex = _file_sha256(path)
    version = ""
    terms: dict[str, GoTerm] = {}
    alt_to_primary: dict[str, str] = {}

    cur_id = ""
    cur_name = ""
    cur_ns = ""
    cur_obs = False
    cur_replaced_by: list[str] = []
    cur_consider: list[str] = []
    cur_alt_ids: list[str] = []
    in_term = False

    def _flush() -> None:
        nonlocal in_term, cur_id, cur_name, cur_ns, cur_obs, cur_replaced_by, cur_consider, cur_alt_ids
        if not in_term:
            return
        if cur_id:
            term = GoTerm(
                id=cur_id,
                name=cur_name,
                namespace=cur_ns,
                aspect=_NAMESPACE_TO_ASPECT.get(cur_ns),
                is_obsolete=cur_obs,
                replaced_by=tuple(cur_replaced_by),
                consider=tuple(cur_consider),
                alt_ids=tuple(cur_alt_ids),
            )
            terms[cur_id] = term
            for alt in cur_alt_ids:
                alt_to_primary[alt] = cur_id
        in_term = False
        cur_id = ""
        cur_name = ""
        cur_ns = ""
        cur_obs = False
        cur_replaced_by = []
        cur_consider = []
        cur_alt_ids = []

    with open_lines_bounded(
        path,
        max_decompressed_bytes=max_bytes,
        max_lines=max_lines,
        max_line_bytes=max_line_bytes,
    ) as lines:
        for _line_no, raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("["):
                _flush()
                in_term = line == "[Term]"
                continue

            if not in_term:
                if line.startswith("data-version:") and not version:
                    version = line.split(":", 1)[1].strip()
                continue

            if ": " not in line:
                continue
            key, val = line.split(": ", 1)
            if key == "id":
                cur_id = val.split()[0].strip().upper()
            elif key == "name":
                cur_name = val.strip()
            elif key == "namespace":
                cur_ns = val.split()[0].strip()
            elif key == "is_obsolete" and val.split()[0].strip().lower() == "true":
                cur_obs = True
            elif key == "alt_id":
                cur_alt_ids.append(val.split()[0].strip().upper())
            elif key == "replaced_by":
                cur_replaced_by.append(val.split()[0].strip().upper())
            elif key == "consider":
                cur_consider.append(val.split()[0].strip().upper())

    _flush()
    return GoOntology(
        version=version,
        sha256=sha256_hex,
        terms=terms,
        alt_to_primary=alt_to_primary,
    )


def clear_go_ontology_cache() -> None:
    """Mengosongkan cache ontologi GO in-process (berguna untuk isolasi test)."""
    _load_go_ontology_cached.cache_clear()


def load_go_ontology(path: str | Path | None = None) -> GoOntology | None:
    """Memuat file OBO GO dari path atau settings.GO_OBO_PATH dengan cache mtime+size."""
    if path is None:
        raw_path = str(settings.GO_OBO_PATH or "").strip()
        resolved = (
            Path(raw_path)
            if raw_path
            else Path(settings.STORAGE_ROOT) / "reference" / "go" / "go-basic.obo"
        )
    else:
        resolved = Path(path)

    if not resolved.is_file():
        logger.warning(
            "Ontologi GO tidak ditemukan (GO_OBO_PATH=%s); domain/nama diambil dari file pemetaan bila ada.",
            resolved,
        )
        return None

    st = resolved.stat()
    return _load_go_ontology_cached(
        str(resolved.resolve()),
        st.st_mtime_ns,
        st.st_size,
        settings.GO_OBO_MAX_BYTES,
        settings.GO_OBO_MAX_LINES,
        settings.UPLOAD_TEXT_MAX_LINE_BYTES,
    )
