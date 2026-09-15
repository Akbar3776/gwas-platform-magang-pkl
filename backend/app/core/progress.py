"""Progress publishing boundary for asynchronous workers."""

import logging

logger = logging.getLogger(__name__)


def publish_progress(job_id: str, step: str, percent: int, message: str) -> None:
    """Log progress until a Redis pub/sub transport is wired in."""
    logger.info("job=%s step=%s percent=%s message=%s", job_id, step, percent, message)