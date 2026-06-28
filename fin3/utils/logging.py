"""Structured logging configuration."""

from __future__ import annotations

import logging

import structlog


def configure_logging(level: str = "INFO", format_: str = "json") -> None:
    """Configure structlog for the fin3 library.

    Sets up structured logging with JSON or console output, timestamps,
    and context injection. Called automatically by ``MarketDataFetcher``
    on initialisation.

    Parameters
    ----------
    level : str
        Logging level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
    format_ : str
        Output format: ``\"json\"`` for structured JSON (production) or
        ``\"console\"`` for human-readable (development).
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            (
                structlog.processors.JSONRenderer()
                if format_ == "json"
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(format="%(message)s", stream=None)
    root = logging.getLogger()
    root.setLevel(level.upper())
