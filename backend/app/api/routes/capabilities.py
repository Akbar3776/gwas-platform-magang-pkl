"""Endpoint for querying tool capabilities (PLINK2, GEMMA, Beagle, R/GAPIT, etc.)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

from fastapi import APIRouter

from app.tasks._common import which_tool

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


@router.get("/capabilities")
def get_pipeline_capabilities() -> dict:
    """Return available computational tools and runtime mode."""
    plink2_ok = which_tool("PLINK2_BINARY_PATH", "plink2") is not None
    plink_ok = which_tool("PLINK_BINARY_PATH", "plink") is not None
    gemma_ok = which_tool("GEMMA_BINARY_PATH", "gemma") is not None
    bcftools_ok = which_tool("BCFTOOLS_BINARY_PATH", "bcftools") is not None

    beagle_jar = Path(os.getenv("BEAGLE_JAR_PATH", "/opt/tools/beagle.jar"))
    has_java = shutil.which("java") is not None
    beagle_ok = beagle_jar.is_file() and has_java

    rscript_ok = which_tool("RSCRIPT_BINARY_PATH", "Rscript") is not None
    enable_multilocus = os.getenv("ENABLE_MULTILOCUS", "false").lower() in {"1", "true", "yes"}
    gapit_ok = rscript_ok and enable_multilocus

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
        "multilocus_available": gapit_ok,
        "mode": runtime_mode,
    }
