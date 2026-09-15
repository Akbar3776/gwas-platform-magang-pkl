"""
core/resource_limits.py

Imputasi (Beagle) adalah tahap paling rakus RAM di pipeline ini. Modul ini
menyediakan DUA lapis proteksi agar container worker Celery untuk queue
"imputation" tidak kena OOM-killed oleh Docker/kernel:

  Lapis 1 — Java heap limit (-Xmx)
      Membatasi heap JVM secara eksplisit. Ini yang paling sering cukup,
      tapi TIDAK menutup seluruh pemakaian memori proses: JVM juga memakai
      memori di luar heap (metaspace, thread stack, JIT code cache, buffer
      native untuk I/O), yang bisa membengkak beberapa ratus MB - 1GB+.

  Lapis 2 — OS-level RLIMIT_AS via preexec_fn (Linux)
      Jaring pengaman keras: kalaupun proses Java mencoba alokasi virtual
      memory melebihi batas total yang kita izinkan (heap + overhead),
      kernel akan menghentikan alokasi tersebut (biasanya proses exit
      dengan error alokasi memori) alih-alih membiarkan RSS terus naik
      sampai cgroup OOM killer membunuh seluruh container secara paksa
      (yang sering membuat Celery worker "hilang" tanpa exception yang
      jelas, sehingga sulit di-debug).

Docker `mem_limit` pada service worker imputation TETAP WAJIB diset sebagai
lapis pertahanan paling luar (lihat IMPUTATION_NOTES.md) — modul ini hanya
memastikan proses Java sendiri berhenti dengan rapi SEBELUM menyentuh batas
itu, bukan menggantikannya.
"""

import os
import resource
from typing import Callable

# Porsi dari total limit memori container yang dialokasikan untuk Java heap.
# Sisanya (~25%) sengaja disisakan untuk metaspace, thread stack, GC overhead,
# dan native buffer yang dipakai Beagle/JVM di luar heap.
HEAP_FRACTION_OF_CONTAINER_LIMIT = 0.75

# Lapis 2 (RLIMIT_AS) diberi headroom tambahan di atas heap, karena virtual
# memory address space JVM selalu lebih besar dari ukuran heap aktualnya
# (mmap library, thread stack, dsb) — kalau RLIMIT_AS diset sama persis
# dengan heap, JVM bisa gagal start sama sekali.
RLIMIT_HEADROOM_FRACTION = 0.3

DEFAULT_CONTAINER_MEM_LIMIT_MB = int(os.environ.get("IMPUTATION_CONTAINER_MEM_LIMIT_MB", "8192"))
MIN_HEAP_MB = 512


def resolve_java_heap_mb(container_mem_limit_mb: int | None = None) -> int:
    """
    Hitung ukuran -Xmx (dalam MB) dari batas memori container.

    `container_mem_limit_mb` idealnya dikirim eksplisit oleh caller sesuai
    `mem_limit` container Docker worker yang menjalankan task ini (mis. lewat
    parameter job atau environment variable per-queue), dengan fallback ke
    `IMPUTATION_CONTAINER_MEM_LIMIT_MB` kalau tidak diberikan.
    """
    limit_mb = container_mem_limit_mb or DEFAULT_CONTAINER_MEM_LIMIT_MB
    heap_mb = int(limit_mb * HEAP_FRACTION_OF_CONTAINER_LIMIT)
    return max(heap_mb, MIN_HEAP_MB)


def resolve_rlimit_as_bytes(heap_mb: int) -> int:
    """Hitung batas RLIMIT_AS (virtual memory) dengan headroom di atas heap."""
    total_mb = heap_mb * (1 + RLIMIT_HEADROOM_FRACTION)
    return int(total_mb * 1024 * 1024)


def make_memory_limited_preexec_fn(virtual_mem_limit_bytes: int) -> Callable[[], None]:
    """
    Kembalikan fungsi untuk `subprocess.Popen(..., preexec_fn=...)` yang
    memasang hard limit RLIMIT_AS pada proses child SEBELUM exec() dipanggil.

    Hanya berfungsi di Linux/Unix (RLIMIT_AS tidak tersedia di Windows) —
    aman dipakai di sini karena target deployment adalah container Docker
    berbasis Linux.
    """

    def _set_limit() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (virtual_mem_limit_bytes, virtual_mem_limit_bytes))

    return _set_limit


def build_java_command(
    jar_path: str,
    jar_args: list[str],
    container_mem_limit_mb: int | None = None,
) -> tuple[list[str], int]:
    """
    Bangun command Java yang siap dijalankan `subprocess.Popen`, sekaligus
    kembalikan batas RLIMIT_AS (bytes) yang harus dipasang sebagai
    `preexec_fn` untuk command tersebut.

    `-XX:+ExitOnOutOfMemoryError` sengaja disertakan: kalau JVM sendiri
    mendeteksi OutOfMemoryError, proses langsung exit dengan kode non-nol
    alih-alih JVM masuk kondisi "zombie"/hang — sehingga Celery task bisa
    mendeteksi kegagalan lewat return code seperti biasa dan memutuskan
    retry atau tidak.
    """
    heap_mb = resolve_java_heap_mb(container_mem_limit_mb)
    rlimit_bytes = resolve_rlimit_as_bytes(heap_mb)

    cmd = [
        "java",
        f"-Xmx{heap_mb}m",
        f"-Xms{min(heap_mb, 1024)}m",  # start kecil, tidak perlu pre-allocate semua heap
        "-XX:+ExitOnOutOfMemoryError",
        "-jar", jar_path,
        *jar_args,
    ]
    return cmd, rlimit_bytes
