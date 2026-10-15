"""File logging for AutoCode itself: ~/.autocode/logs/autocode.log, rotated."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import CONFIG_HOME

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(threadName)s %(message)s"
MAX_LOG_BYTES = 2_000_000
LOG_BACKUPS = 3


def setup_logging(level: int = logging.INFO) -> Path | None:
    """Attach a rotating file handler to the `autocode` logger. Returns the log path, or None if unwritable."""
    log_file = CONFIG_HOME / "logs" / "autocode.log"
    logger = logging.getLogger("autocode")
    if any(isinstance(handler, RotatingFileHandler) for handler in logger.handlers):
        return log_file
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_file, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(level)
    return log_file
