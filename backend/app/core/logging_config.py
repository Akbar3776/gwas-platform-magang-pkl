"""Container-friendly application logging configuration."""

from __future__ import annotations

import logging
import logging.config
import os
import sys


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "default",
                }
            },
            "root": {"level": level, "handlers": ["console"]},
            "loggers": {
                "uvicorn.error": {"level": level, "propagate": True},
                "uvicorn.access": {"level": level, "propagate": True},
                "celery": {"level": level, "propagate": True},
            },
        }
    )
