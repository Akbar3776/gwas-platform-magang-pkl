"""
Service untuk menjalankan PLINK sebagai subprocess dan menangkap
output-nya secara streaming (bukan `subprocess.run()` yang blocking dan
menahan seluruh output di memori sampai proses selesai).

Prinsip desain (memenuhi Development Rule #1):
- stdout/stderr dibaca baris demi baris dan langsung ditulis ke file log
  di disk — log PLINK untuk VCF berukuran GB bisa sangat panjang, sehingga
  TIDAK BOLEH ditampung penuh sebagai string/list di memori Python.
- Hanya beberapa baris terakhir (`_LOG_TAIL_SIZE`) yang disimpan di memori,
  untuk dipakai sebagai ringkasan error jika proses gagal.
"""

import subprocess
from collections import deque
from pathlib import Path
from typing import Callable

# Jumlah baris log terakhir yang disimpan di memori untuk ringkasan error.
# Cukup kecil dan konstan, tidak bergantung pada ukuran file VCF.
_LOG_TAIL_SIZE = 50


class PlinkExecutionError(RuntimeError):
    """Dilempar jika proses PLINK keluar dengan return code bukan 0."""

    def __init__(self, message: str, log_tail: str):
        super().__init__(message)
        self.log_tail = log_tail


def build_plink_qc_command(
    input_vcf_path: str,
    output_prefix: str,
    maf: float = 0.01,
    geno: float = 0.05,
    mind: float = 0.1,
    hwe: float = 1e-6,
) -> list[str]:
    """
    Menyusun command PLINK untuk tahap Quality Control dasar.

    - --maf   : buang variant dengan frekuensi alel minor di bawah threshold
    - --geno  : buang variant dengan missing-rate di atas threshold
    - --mind  : buang sampel/individu dengan missing-rate di atas threshold
    - --hwe   : buang variant yang melanggar Hardy-Weinberg equilibrium
    """
    return [
        "plink",
        "--vcf", input_vcf_path,
        "--maf", str(maf),
        "--geno", str(geno),
        "--mind", str(mind),
        "--hwe", str(hwe),
        "--make-bed",
        "--recode", "vcf", "bgz",
        "--out", output_prefix,
    ]


def run_subprocess_with_logging(
    cmd: list[str],
    log_file_path: Path,
    timeout_seconds: int,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """
    Menjalankan `cmd` sebagai subprocess, streaming stdout+stderr baris demi
    baris langsung ke `log_file_path` di disk.

    Args:
        on_line: callback opsional dipanggil untuk setiap baris log baru —
            dipakai task Celery untuk parsing progress tanpa perlu
            `run_subprocess_with_logging` tahu apa pun soal Celery/DB.

    Returns:
        Ringkasan `_LOG_TAIL_SIZE` baris terakhir dari log (untuk pesan sukses/gagal).

    Raises:
        PlinkExecutionError: jika return code subprocess bukan 0.
        subprocess.TimeoutExpired: jika proses melebihi `timeout_seconds`
            (subprocess sudah dipastikan di-terminate sebelum exception dilempar).
    """
    log_file_path.parent.mkdir(parents=True, exist_ok=True)
    log_tail: deque[str] = deque(maxlen=_LOG_TAIL_SIZE)

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # gabungkan stderr ke stdout agar urutan log tetap kronologis
        text=True,
        bufsize=1,  # line-buffered
    )

    try:
        with open(log_file_path, "a", encoding="utf-8") as log_file:
            # Iterasi baris demi baris — TIDAK menahan seluruh output di memori.
            for line in process.stdout:
                log_file.write(line)
                log_file.flush()
                log_tail.append(line.rstrip("\n"))
                if on_line is not None:
                    on_line(line.rstrip("\n"))

            try:
                return_code = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()  # pastikan proses zombie benar-benar dibersihkan
                raise

    finally:
        if process.stdout:
            process.stdout.close()

    tail_text = "\n".join(log_tail)

    if return_code != 0:
        raise PlinkExecutionError(
            f"PLINK keluar dengan return code {return_code}", log_tail=tail_text
        )

    return tail_text
