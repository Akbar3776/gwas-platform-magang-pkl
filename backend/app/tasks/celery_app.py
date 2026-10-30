"""
Instance Celery app terpusat.

Semua task (upload assembly, normalisasi, QC, imputasi, GWAS) didaftarkan
lewat parameter `include` di sini, agar worker (`celery -A app.tasks.celery_app worker`)
otomatis menemukan seluruh task tanpa import manual di tempat lain.
"""

from __future__ import annotations

import functools
import logging
import threading
import uuid
from typing import Any, Callable

from app.core.config import settings

try:
    from celery import Celery, Task, chain
    from celery.canvas import Signature
    from celery.exceptions import SoftTimeLimitExceeded
    from celery.schedules import crontab
    from celery.utils.log import get_task_logger

    _HAS_CELERY = True
except ImportError:  # pragma: no cover - local fallback when celery is not installed
    _HAS_CELERY = False

    class SoftTimeLimitExceeded(Exception):  # type: ignore[no-redef]
        pass

    class Task:  # type: ignore[no-redef]
        name: str = ""

        def retry(self, exc: Exception | None = None, countdown: int = 0):
            if exc is not None:
                raise exc
            raise RuntimeError("Task retry requested")

    class _AsyncResult:
        def __init__(self, task_id: str | None = None):
            self.id = task_id or str(uuid.uuid4())

    class Signature:  # type: ignore[no-redef]
        def __init__(self, task_wrapper: "_LocalTask", args: tuple = (), kwargs: dict | None = None):
            self.task_wrapper = task_wrapper
            self.args = args
            self.kwargs = kwargs or {}

        def apply_async(self) -> _AsyncResult:
            task_id = str(uuid.uuid4())

            def _runner():
                self.task_wrapper(*self.args, **self.kwargs)

            threading.Thread(target=_runner, daemon=True).start()
            return _AsyncResult(task_id)

    class _LocalChain(Signature):
        def __init__(self, *signatures: Signature):
            self.signatures = signatures

        def apply_async(self) -> _AsyncResult:
            task_id = str(uuid.uuid4())

            def _runner():
                prev_result = None
                for index, sig in enumerate(self.signatures):
                    call_args = sig.args if index == 0 else (prev_result, *sig.args)
                    prev_result = sig.task_wrapper(*call_args, **sig.kwargs)

            threading.Thread(target=_runner, daemon=True).start()
            return _AsyncResult(task_id)

    def chain(*signatures: Signature) -> _LocalChain:  # type: ignore[misc]
        return _LocalChain(*signatures)

    class _LocalTask:
        def __init__(self, func: Callable, *, bind: bool = False, name: str | None = None, base: type = Task):
            self._func = func
            self._bind = bind
            self.name = name or func.__name__
            self._self_obj = base()
            self._self_obj.name = self.name
            functools.update_wrapper(self, func)

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            if self._bind:
                return self._func(self._self_obj, *args, **kwargs)
            return self._func(*args, **kwargs)

        def delay(self, *args: Any, **kwargs: Any) -> _AsyncResult:
            return self.s(*args, **kwargs).apply_async()

        def apply_async(self, args: tuple = (), kwargs: dict | None = None) -> _AsyncResult:
            return self.s(*args, **(kwargs or {})).apply_async()

        def s(self, *args: Any, **kwargs: Any) -> Signature:
            return Signature(self, args=args, kwargs=kwargs)

    class _LocalCelery:
        def __init__(self, name: str, **kwargs: Any):
            self.main = name
            self.conf = type("Conf", (), {"update": lambda self, **kw: None, "beat_schedule": {}})()

        def task(self, *args: Any, **opts: Any):
            def _decorator(func: Callable) -> _LocalTask:
                return _LocalTask(
                    func,
                    bind=bool(opts.get("bind", False)),
                    name=opts.get("name"),
                    base=opts.get("base", Task),
                )

            if args and callable(args[0]):
                return _decorator(args[0])
            return _decorator

    def crontab(**kwargs: Any) -> dict[str, Any]:  # type: ignore[misc]
        return kwargs

    def get_task_logger(name: str) -> logging.Logger:  # type: ignore[misc]
        return logging.getLogger(name)

    Celery = _LocalCelery  # type: ignore[misc,assignment]


celery_app = Celery(
    "gwas_pipeline",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.upload_tasks",
        "app.tasks.inspection_tasks",
        "app.tasks.normalization_tasks",
        "app.tasks.qc_tasks",
        "app.tasks.imputation_tasks",
        "app.tasks.structure_tasks",
        "app.tasks.gemma_tasks",
        "app.tasks.multilocus_tasks",
        "app.tasks.postgwas_tasks",
        "app.tasks.visualization_tasks",
        "app.tasks.annotation_tasks",
        "app.tasks.cleanup_tasks",
        "app.tasks.retention_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    # acks_late + reject_on_worker_lost: task yang belum selesai saat worker
    # crash akan otomatis di-requeue ke worker lain, bukan hilang begitu saja.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

celery_app.conf.beat_schedule = {
    "purge-expired-datasets-and-jobs": {
        "task": "maintenance.purge_expired_records",  # <-- nama task, BUKAN "app.tasks.retention_tasks.purge_expired_records"
        "schedule": crontab(hour=0, minute=0),
    },
}