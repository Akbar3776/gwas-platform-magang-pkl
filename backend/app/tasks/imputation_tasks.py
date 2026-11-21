"""Celery task for memory-bounded Beagle imputation (Fase Imputation)."""

from __future__ import annotations

import gzip
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.resource_limits import read_container_memory_limit_mb, resolve_beagle_memory_flags
from app.db.engine.database import SessionLocal
from app.db.engine.models import AnalysisJob, JobStatus, PipelineStep, ResultType
from app.services.qc_service import MAX_32BIT_POS, _is_gzip_file
from app.tasks._common import (
    JobCancelledError,
    append_log,
    ensure_not_cancelled,
    is_job_cancelled,
    mark_job,
    stage_dir,
    upsert_result,
    which_tool,
    write_json,
    write_stage_summary,
)
from app.tasks.celery_app import celery_app, get_task_logger
from app.utils.process import CLICancelledError, CLIExecutionError, run_cli_command

logger = get_task_logger(__name__)


BEAGLE_JAR_PATH = which_tool("beagle_jar") or "/opt/tools/beagle.jar"
IMPUTATION_TIMEOUT_SECONDS = int(os.getenv("IMPUTATION_TIMEOUT_SECONDS", str(6 * 60 * 60)))
BEAGLE_MAX_MEMORY = os.getenv("BEAGLE_MAX_MEMORY", "")


def _prepare_beagle_safe_vcf(
    qc_vcf_path: str,
    workspace: Path,
    max_pos: int = MAX_32BIT_POS,
    min_positions_per_chrom: int = 2,
) -> tuple[str, int, int]:
    """
    Menyiapkan berkas VCF yang aman untuk Beagle 5.
    1. Memeriksa varian dengan POS > max_pos (mencegah Java 32-bit integer overflow).
    2. Memeriksa kromosom/scaffold dengan < min_positions_per_chrom posisi unik
       (mencegah java.lang.IllegalArgumentException: Window has only one position pada Beagle 5).

    Varian pada scaffold ber-marker tunggal diabaikan dari imputasi Beagle karena ketiadaan
    linkage disequilibrium untuk imputasi/phasing. Hal ini memungkinkan seluruh kromosom utama
    dan scaffold multi-marker diimputasi dengan sempurna dan menghasilkan berkas BGZF murni
    yang kompatibel dengan PLINK 2.

    Returns:
        (effective_vcf_path, overflow_removed_count, singles_count)
    """
    src = Path(qc_vcf_path)
    if not src.is_file():
        return str(qc_vcf_path), 0, 0

    in_open = gzip.open if _is_gzip_file(src) else open

    # Pass 1: Cek posisi unik per contig dan keberadaan overflow secara streaming (O(1) memory)
    pos_per_chrom: dict[str, set[int]] = {}
    has_overflow = False
    with in_open(src, "rt", encoding="utf-8", errors="replace") as fin:
        for line in fin:
            if line.startswith("#"):
                continue
            tab1 = line.find("\t")
            if tab1 == -1:
                continue
            tab2 = line.find("\t", tab1 + 1)
            if tab2 == -1:
                continue
            chrom = line[:tab1]
            try:
                pos = int(line[tab1 + 1:tab2])
            except ValueError:
                continue

            if pos > max_pos:
                has_overflow = True
                continue

            s = pos_per_chrom.setdefault(chrom, set())
            if len(s) < min_positions_per_chrom:
                s.add(pos)

    single_pos_chroms = {c for c, s in pos_per_chrom.items() if len(s) < min_positions_per_chrom}

    # Jika tidak ada overflow dan tidak ada contig ber-marker tunggal, gunakan berkas asli
    if not has_overflow and not single_pos_chroms:
        return str(qc_vcf_path), 0, 0

    # Pass 2: Tulis berkas aman untuk Beagle
    safe_vcf = workspace / "beagle_input_safe.vcf.gz"
    overflow_removed = 0
    singles_count = 0
    beagle_variants_count = 0

    with (
        in_open(src, "rt", encoding="utf-8", errors="replace") as fin,
        gzip.open(safe_vcf, "wt", encoding="utf-8") as f_safe,
    ):
        for line in fin:
            if line.startswith("#"):
                f_safe.write(line)
                continue
            tab1 = line.find("\t")
            if tab1 == -1:
                continue
            tab2 = line.find("\t", tab1 + 1)
            if tab2 == -1:
                continue
            chrom = line[:tab1]
            try:
                pos = int(line[tab1 + 1:tab2])
            except ValueError:
                continue

            if pos > max_pos:
                overflow_removed += 1
                continue

            if chrom in single_pos_chroms:
                singles_count += 1
            else:
                f_safe.write(line)
                beagle_variants_count += 1

    if beagle_variants_count == 0:
        safe_vcf.unlink(missing_ok=True)
        return str(qc_vcf_path), overflow_removed, singles_count

    return str(safe_vcf), overflow_removed, singles_count


def _beagle_command(
    qc_vcf_path: str,
    reference_vcf_path: str | None,
    output_prefix: str,
    params: dict[str, Any],
    warning_sink: list[str] | None = None,
) -> list[str]:
    nthreads = int(params.get("nthreads", 2))
    beagle_jar = which_tool("beagle_jar") or BEAGLE_JAR_PATH
    java_bin = which_tool("java") or "java"
    flags, warning = resolve_beagle_memory_flags(
        params.get("beagle_max_memory") or BEAGLE_MAX_MEMORY,
        read_container_memory_limit_mb(),
    )
    if warning and warning_sink is not None:
        warning_sink.append(warning)
    command = [java_bin, *flags, "-jar", beagle_jar]
    command.extend([
        f"gt={qc_vcf_path}",
        f"out={output_prefix}",
        f"nthreads={nthreads}",
    ])
    if reference_vcf_path:
        command.append(f"ref={reference_vcf_path}")
    if params.get("chromosome"):
        command.append(f"chrom={params['chromosome']}")
    if params.get("map_path"):
        command.append(f"map={params['map_path']}")
    if params.get("window") is not None:
        command.append(f"window={params['window']}")
    if params.get("overlap") is not None:
        command.append(f"overlap={params['overlap']}")
    return command


def _record_imputation_stage_summary(
    job_id: str,
    job: AnalysisJob,
    db: Any,
    *,
    status: str,
    beagle_guard_removed: int,
    singles_count: int,
    reason: str | None = None,
) -> None:
    """Persist Beagle guard counts on job.parameters and write imputation/stage_summary.json."""
    job.parameters = {
        **(job.parameters or {}),
        "beagle_guard_removed": int(beagle_guard_removed),
    }
    payload: dict[str, Any] = {
        "status": status,
        "beagle_guard_removed": int(beagle_guard_removed),
        "singles_count": int(singles_count),
        "counts": {
            "beagle_guard_removed": int(beagle_guard_removed),
            "singles_removed": int(singles_count),
        },
        "warnings": (
            [f"beagle_guard_removed={int(beagle_guard_removed)}"]
            if int(beagle_guard_removed) > 0
            else []
        ),
    }
    if reason:
        payload["reason"] = reason
    write_stage_summary(job_id, "imputation", payload, db=db)


@celery_app.task(
    bind=True,
    name="app.tasks.imputation_tasks.run_imputation",
    queue="imputation",
    max_retries=0,
    acks_late=True,
    soft_time_limit=IMPUTATION_TIMEOUT_SECONDS + 30,
    time_limit=IMPUTATION_TIMEOUT_SECONDS + 120,
)
def run_imputation(
    self,
    qc_result: dict[str, Any],
    job_id: str,
    reference_vcf_path: str | None = None,
    params: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Run Beagle imputation with explicit JVM and memory bounds.
    Supports mode="none" (honest skip), mode="reference", and mode="reference_free".
    """
    db = SessionLocal()
    ctx: dict[str, Any] = dict(qc_result or {})
    try:
        ensure_not_cancelled(job_id, db, run_id=run_id)
        job = db.get(AnalysisJob, job_id)
        if job is None:
            raise ValueError(f"AnalysisJob {job_id} tidak ditemukan")

        qc_vcf_path = ctx.get("qc_vcf_path") or ctx.get("output_vcf") or ctx.get("genotype_vcf_path")
        qc_bfile_prefix = ctx.get("qc_bfile_prefix") or ctx.get("output_prefix")
        if not qc_vcf_path or not Path(qc_vcf_path).is_file():
            raise FileNotFoundError(f"VCF hasil QC tidak ditemukan: {qc_vcf_path}")

        workspace = stage_dir(job_id, "imputation")
        output_prefix = workspace / "imputed"
        log_file = workspace / "beagle.log"

        imputation_cfg = (job.parameters or {}).get("imputation") or params or {}
        mode = imputation_cfg.get("mode", "none")
        ref_vcf = imputation_cfg.get("reference_vcf_path") or reference_vcf_path

        # Mode: None -> Skip jujur tanpa menduplikasi data palsu
        if mode == "none":
            append_log(
                log_file,
                f"=== TAHAP 4: IMPUTASI GENOTIPE (Beagle 5) ===\n"
                f"Waktu        : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"[SKIPPED] Imputasi dilewati oleh pengguna (mode='none').\n"
                f"Menggunakan data genotipe hasil Quality Control secara langsung.\n"
            )
            write_json(workspace / "skipped.json", {
                "status": "skipped",
                "reason": "Imputasi tidak diminta (mode=none)",
            })

            _record_imputation_stage_summary(
                job_id,
                job,
                db,
                status="skipped",
                beagle_guard_removed=0,
                singles_count=0,
                reason="Imputasi tidak diminta (mode=none)",
            )
            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_STRUCTURE,
                current_step=PipelineStep.STRUCTURE,
                work_dir=str(workspace),
            )

            return {
                **ctx,
                "status": "skipped",
                "imputation_skipped": True,
                "output_vcf": str(qc_vcf_path),
                "genotype_vcf_path": str(qc_vcf_path),
                "genotype_bfile_prefix": str(qc_bfile_prefix) if qc_bfile_prefix else None,
                "log_file": str(log_file),
            }

        # Mode reference atau reference_free
        beagle_jar_path = which_tool("beagle_jar") or BEAGLE_JAR_PATH
        can_run_beagle = (
            bool(which_tool("java"))
            and Path(beagle_jar_path).is_file()
            and (mode != "reference" or (bool(ref_vcf) and Path(ref_vcf).is_file()))
        )

        mark_job(
            db,
            job,
            overall_status=JobStatus.RUNNING_IMPUTATION,
            current_step=PipelineStep.IMPUTATION,
            work_dir=str(workspace),
            imputed_prefix=str(output_prefix),
        )

        effective_vcf_path, overflow_filtered, singles_count = _prepare_beagle_safe_vcf(
            str(qc_vcf_path), workspace, max_pos=MAX_32BIT_POS
        )
        if overflow_filtered > 0:
            append_log(
                log_file,
                f"[WARN] Terdeteksi {overflow_filtered:,} varian pada rentang koordinat batas "
                f"(2.100.000.001-2.147.483.647 bp atau > {MAX_32BIT_POS:,} bp pada kromosom yang tidak dipecah).\n"
                f"Varian disaring ke berkas aman ({Path(effective_vcf_path).name}) untuk mencegah "
                f"Java 32-bit sliding window overflow pada Beagle (vcf.PositionMap.basePos).\n"
            )
        if singles_count > 0:
            append_log(
                log_file,
                f"[INFO] Terdeteksi {singles_count:,} varian pada scaffold dengan < 2 posisi marker.\n"
                f"Scaffold tanpa linkage tidak dapat diproses oleh Beagle 5 (MarkerMap.meanSingleBaseGenDist) "
                f"dan diabaikan dari imputasi agar Beagle memfasekan seluruh kromosom utama secara optimal.\n"
            )

        memory_warnings: list[str] = []
        command = _beagle_command(
            effective_vcf_path,
            ref_vcf if mode == "reference" else None,
            str(output_prefix),
            imputation_cfg,
            warning_sink=memory_warnings,
        )

        for warning in memory_warnings:
            append_log(log_file, f"{warning}\n")

        append_log(
            log_file,
            f"=== TAHAP 4: IMPUTASI GENOTIPE (Beagle 5) ===\n"
            f"Waktu mulai     : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"Mode            : {mode}\n"
            f"Input QC VCF    : {effective_vcf_path}\n"
            f"Reference Panel : {ref_vcf or '(tanpa reference - reference-free)'}\n"
            f"Command         : {' '.join(command)}\n"
        )

        if can_run_beagle:
            def _cancel_check() -> bool:
                return is_job_cancelled(job_id, run_id=run_id)

            try:
                run_cli_command(
                    command,
                    cwd=workspace,
                    log_file=log_file,
                    timeout=IMPUTATION_TIMEOUT_SECONDS,
                    cancel_check=_cancel_check,
                )
            except (JobCancelledError, CLICancelledError):
                raise
            except CLIExecutionError as exc:
                err_text = " ".join(exc.last_lines)
                is_beagle_overflow = (
                    "exceeds the maximum integer value" in err_text
                    or "PositionMap.basePos" in err_text
                )
                is_single_marker_error = (
                    "Window has only one position" in err_text
                    or "MarkerMap.meanSingleBaseGenDist" in err_text
                )
                if is_beagle_overflow or is_single_marker_error:
                    reason = (
                        "Beagle integer overflow (koordinat genom melebihi batas 2.14 Gb)"
                        if is_beagle_overflow
                        else "Beagle single-marker scaffold (scaffold memiliki < 2 posisi marker)"
                    )
                    append_log(
                        log_file,
                        f"\n[WARNING] Beagle 5 mengalami kendala topologi genom: {reason}.\n"
                        "Beagle tidak mendukung kromosom atau konfigurasi scaffold seperti ini.\n"
                        f"Secara otomatis melanjutkan pipeline menggunakan data genotipe hasil QC ({qc_vcf_path}).\n"
                    )
                    write_json(workspace / "skipped.json", {
                        "status": "skipped",
                        "reason": reason,
                    })
                    _record_imputation_stage_summary(
                        job_id,
                        job,
                        db,
                        status="skipped",
                        beagle_guard_removed=overflow_filtered,
                        singles_count=singles_count,
                        reason=reason,
                    )
                    mark_job(
                        db,
                        job,
                        overall_status=JobStatus.RUNNING_STRUCTURE,
                        current_step=PipelineStep.STRUCTURE,
                    )
                    return {
                        **ctx,
                        "status": "skipped",
                        "imputation_skipped": True,
                        "output_vcf": str(qc_vcf_path),
                        "genotype_vcf_path": str(qc_vcf_path),
                        "genotype_bfile_prefix": str(qc_bfile_prefix) if qc_bfile_prefix else None,
                        "log_file": str(log_file),
                    }
                raise

            output_vcf = Path(f"{output_prefix}.vcf.gz")
            if not output_vcf.is_file():
                raise RuntimeError(f"Beagle selesai tetapi output tidak ditemukan: {output_vcf}")

            # Konversi balik ke PLINK biner sekaligus filter ulang MAF/geno pasca-imputasi
            qc_cfg = (job.parameters or {}).get("qc") or ctx.get("qc_params") or {}
            maf_val = qc_cfg.get("maf", 0.05) if isinstance(qc_cfg, dict) else 0.05
            geno_val = qc_cfg.get("geno", 0.05) if isinstance(qc_cfg, dict) else 0.05

            plink2_bin = which_tool("plink2") or which_tool("plink")
            if plink2_bin:
                conv_cmd = [
                    plink2_bin,
                    "--vcf", str(output_vcf),
                    "--double-id",
                    "--allow-extra-chr",
                ]
                if maf_val is not None:
                    conv_cmd.extend(["--maf", str(maf_val)])
                if geno_val is not None:
                    conv_cmd.extend(["--geno", str(geno_val)])
                conv_cmd.extend(["--make-bed", "--out", str(output_prefix)])
                append_log(
                    log_file,
                    f"Konversi & QC ulang pasca-imputasi (maf={maf_val}, geno={geno_val}): {' '.join(conv_cmd)}",
                )
                run_cli_command(
                    conv_cmd,
                    cwd=workspace,
                    log_file=log_file,
                    timeout=IMPUTATION_TIMEOUT_SECONDS,
                    cancel_check=_cancel_check,
                )

            append_log(log_file, f"[SUCCESS] Imputasi selesai: {output_vcf} ({output_vcf.stat().st_size} bytes).\n")
            upsert_result(db, job_id, ResultType.IMPUTED_VCF, str(output_vcf))

            _record_imputation_stage_summary(
                job_id,
                job,
                db,
                status="completed",
                beagle_guard_removed=overflow_filtered,
                singles_count=singles_count,
            )
            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_STRUCTURE,
                current_step=PipelineStep.STRUCTURE,
            )

            return {
                **ctx,
                "status": "completed",
                "imputation_skipped": False,
                "beagle_guard_removed": int(overflow_filtered),
                "output_vcf": str(output_vcf),
                "genotype_vcf_path": str(output_vcf),
                "genotype_bfile_prefix": str(output_prefix),
                "log_file": str(log_file),
            }
        else:
            # Beagle/Java atau reference panel tidak tersedia di lingkungan saat ini
            append_log(
                log_file,
                f"[SKIPPED] Beagle/Java runtime atau panel referensi tidak ditemukan di host ini.\n"
                f"Melanjutkan analisis pipeline dengan data genotipe hasil QC ({qc_vcf_path}).\n"
            )
            write_json(workspace / "skipped.json", {
                "status": "skipped",
                "reason": "Beagle JAR/Java atau reference panel tidak tersedia di host",
            })

            _record_imputation_stage_summary(
                job_id,
                job,
                db,
                status="skipped",
                beagle_guard_removed=overflow_filtered,
                singles_count=singles_count,
                reason="Beagle JAR/Java atau reference panel tidak tersedia di host",
            )
            mark_job(
                db,
                job,
                overall_status=JobStatus.RUNNING_STRUCTURE,
                current_step=PipelineStep.STRUCTURE,
            )

            return {
                **ctx,
                "status": "skipped",
                "imputation_skipped": True,
                "beagle_guard_removed": int(overflow_filtered),
                "output_vcf": str(qc_vcf_path),
                "genotype_vcf_path": str(qc_vcf_path),
                "genotype_bfile_prefix": str(qc_bfile_prefix) if qc_bfile_prefix else None,
                "log_file": str(log_file),
            }

    except (JobCancelledError, CLICancelledError):
        logger.info("Imputasi dibatalkan: job=%s", job_id)
        raise
    except Exception as exc:
        db.rollback()
        message = str(exc)
        if isinstance(exc, CLIExecutionError):
            message = f"{exc}\n\n--- Log terakhir ---\n{''.join(exc.last_lines).strip()}"
        job = db.get(AnalysisJob, job_id)
        if job is not None:
            mark_job(db, job, overall_status=JobStatus.FAILED, failed_step="imputation", error_message=message)
        logger.exception("Imputasi gagal untuk job=%s", job_id)
        raise
    finally:
        db.close()
