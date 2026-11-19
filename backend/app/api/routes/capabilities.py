"""Endpoint for querying tool capabilities (PLINK2, GEMMA, Beagle, R/GAPIT, etc.)."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from app.tasks._common import which_tool

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/capabilities")
def get_pipeline_capabilities() -> dict:
    """Return available computational tools and runtime mode."""
    plink2_ok = which_tool("plink2") is not None
    plink_ok = which_tool("plink") is not None
    gemma_ok = which_tool("gemma") is not None
    bcftools_ok = which_tool("bcftools") is not None

    beagle_jar = which_tool("beagle_jar")
    has_java = which_tool("java") is not None
    beagle_ok = bool(beagle_jar) and Path(beagle_jar).is_file() and has_java

    rscript_ok = which_tool("Rscript") is not None
    python_multilocus_allowed = os.getenv("ALLOW_PYTHON_MULTILOCUS", "true").lower() in {"1", "true", "yes"}
    gapit_runner = Path(__file__).resolve().parents[2] / "templates" / "gapit_runner.R"
    gapit_ok = rscript_ok and gapit_runner.is_file()
    multilocus_available = bool(rscript_ok or python_multilocus_allowed)

    in_docker = Path("/.dockerenv").is_file() or os.getenv("CONTAINER_MODE", "false").lower() == "true"
    runtime_mode = "docker" if in_docker else "local-fallback"

    return {
        "plink2": plink2_ok,
        "plink": plink_ok,
        "gemma": gemma_ok,
        "bcftools": bcftools_ok,
        "beagle": beagle_ok,
        "rscript": rscript_ok,
        "gapit": gapit_ok,
        "multilocus_available": multilocus_available,
        "supported_models": ["GLM", "LMM", "FarmCPU", "BLINK", "MLMM"],
        "multilocus_models": ["FarmCPU", "BLINK", "MLMM"],
        "mode": runtime_mode,
    }
