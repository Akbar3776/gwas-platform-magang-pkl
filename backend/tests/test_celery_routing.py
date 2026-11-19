import ast
from pathlib import Path
import re

import pytest

WORKER_QUEUES = {"celery", "normalization", "qc", "imputation", "gwas"}
REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "backend" / "app" / "tasks"
STAGE_MODULES = [
    "inspection_tasks",
    "normalization_tasks",
    "qc_tasks",
    "imputation_tasks",
    "structure_tasks",
    "gemma_tasks",
    "multilocus_tasks",
    "postgwas_tasks",
    "visualization_tasks",
    "annotation_tasks",
]


def _extract_compose_worker_queues(compose_path: Path) -> set[str]:
    text = compose_path.read_text(encoding="utf-8")
    matches = re.findall(r"(?:^|\s)-Q\s+([A-Za-z0-9_,.-]+)", text)
    assert matches, f"Flag -Q worker tidak ditemukan di {compose_path}"
    queues: set[str] = set()
    for group in matches:
        queues.update(q.strip() for q in group.split(",") if q.strip())
    return queues


def _extract_declared_queues_from_source(module_name: str) -> list[tuple[str, str]]:
    source_path = TASKS_DIR / f"{module_name}.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    found: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "queue" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            found.append((f"{module_name}.{node.name}", kw.value.value))
    return found


def test_declared_task_queues_covered_by_compose():
    dev_queues = _extract_compose_worker_queues(REPO_ROOT / "infra" / "docker-compose.yml")
    prod_queues = _extract_compose_worker_queues(REPO_ROOT / "infra" / "docker-compose.prod.yml")
    assert WORKER_QUEUES.issubset(dev_queues)
    assert WORKER_QUEUES.issubset(prod_queues)

    declared: list[tuple[str, str]] = []
    for mod in STAGE_MODULES:
        declared.extend(_extract_declared_queues_from_source(mod))
    assert len(declared) >= len(STAGE_MODULES)

    for task_label, queue_name in declared:
        assert queue_name in WORKER_QUEUES, f"{task_label} memakai queue {queue_name!r} di luar {WORKER_QUEUES}"
        assert queue_name in dev_queues, f"{task_label} queue {queue_name!r} tidak ada di docker-compose.yml"
        assert queue_name in prod_queues, f"{task_label} queue {queue_name!r} tidak ada di docker-compose.prod.yml"


def test_celery_task_queue_attributes_covered_by_compose():
    pytest.importorskip("celery")
    from app.tasks import (
        annotation_tasks,
        gemma_tasks,
        imputation_tasks,
        inspection_tasks,
        multilocus_tasks,
        normalization_tasks,
        postgwas_tasks,
        qc_tasks,
        structure_tasks,
        visualization_tasks,
    )

    stage_tasks = [
        inspection_tasks.run_inspection,
        normalization_tasks.run_vcf_normalization,
        qc_tasks.run_qc_pipeline,
        imputation_tasks.run_imputation,
        structure_tasks.run_structure_analysis,
        gemma_tasks.run_gemma_gwas,
        multilocus_tasks.run_multilocus_gwas,
        postgwas_tasks.run_postgwas_analysis,
        visualization_tasks.run_visualization,
        annotation_tasks.run_annotation,
    ]
    dev_queues = _extract_compose_worker_queues(REPO_ROOT / "infra" / "docker-compose.yml")
    prod_queues = _extract_compose_worker_queues(REPO_ROOT / "infra" / "docker-compose.prod.yml")

    for task in stage_tasks:
        q = getattr(task, "queue", None) or "celery"
        assert q in WORKER_QUEUES
        assert q in dev_queues
        assert q in prod_queues


def _eval_static_expr(node: ast.AST):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _eval_static_expr(node.left) * _eval_static_expr(node.right)
    if isinstance(node, ast.Dict):
        return {_eval_static_expr(k): _eval_static_expr(v) for k, v in zip(node.keys, node.values) if k is not None}
    raise ValueError(f"Unsupported AST node: {ast.dump(node)}")


def test_celery_visibility_timeout_source_configured():
    from app.tasks.gemma_tasks import GEMMA_TIMEOUT_SECONDS
    from app.tasks.imputation_tasks import IMPUTATION_TIMEOUT_SECONDS

    celery_src = (TASKS_DIR / "celery_app.py").read_text(encoding="utf-8")
    tree = ast.parse(celery_src, filename="celery_app.py")
    conf_kwargs: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
        ):
            for kw in node.keywords:
                if kw.arg:
                    conf_kwargs[kw.arg] = kw.value

    assert "broker_transport_options" in conf_kwargs
    assert "result_backend_transport_options" in conf_kwargs
    assert "worker_prefetch_multiplier" in conf_kwargs

    prefetch_val = _eval_static_expr(conf_kwargs["worker_prefetch_multiplier"])
    assert prefetch_val == 1

    for key in ("broker_transport_options", "result_backend_transport_options"):
        opts = _eval_static_expr(conf_kwargs[key])
        assert opts["visibility_timeout"] > IMPUTATION_TIMEOUT_SECONDS + 120
        assert opts["visibility_timeout"] > GEMMA_TIMEOUT_SECONDS + 120


def test_celery_visibility_timeout_exceeds_longest_task():
    pytest.importorskip("celery")
    from app.tasks.celery_app import celery_app
    from app.tasks.gemma_tasks import GEMMA_TIMEOUT_SECONDS
    from app.tasks.imputation_tasks import IMPUTATION_TIMEOUT_SECONDS

    broker_timeout = celery_app.conf.broker_transport_options["visibility_timeout"]
    backend_timeout = celery_app.conf.result_backend_transport_options["visibility_timeout"]
    assert broker_timeout > IMPUTATION_TIMEOUT_SECONDS + 120
    assert broker_timeout > GEMMA_TIMEOUT_SECONDS + 120
    assert backend_timeout > IMPUTATION_TIMEOUT_SECONDS + 120
    assert backend_timeout > GEMMA_TIMEOUT_SECONDS + 120
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_single_cleanup_hook_configured_in_celery_app():
    celery_src = (TASKS_DIR / "celery_app.py").read_text(encoding="utf-8")
    assert "task_postrun" not in celery_src
    assert "_on_celery_task_postrun" not in celery_src
    assert celery_src.count("_cleanup_deleted_job_after_task(kwargs.get(\"job_id\"))") <= 2
