"""Structured logging setup for AuCl."""

from __future__ import annotations

import logging
import sys

from modules.common.settings import LoggingConfig


def setup_logging(config: LoggingConfig | None = None) -> None:
    cfg = config or LoggingConfig()
    level = getattr(logging, cfg.level.upper(), logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(cfg.format))
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
