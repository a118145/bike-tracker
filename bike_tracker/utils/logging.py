"""structlog setup.

``configure_logging()`` is called once from the app module. Everything else
uses ``get_logger(__name__)`` and binds its own context, so every line carries
who emitted it without repeating that in each call.

Convention: log *events*, not sentences. ``log.info("item.create", item_id=7)``
— never ``log.info(f"created item {id}")``. Event names stay greppable and the
key/value pairs stay machine-readable when JSON rendering is on.
"""

import logging
import os

import structlog
from structlog.stdlib import BoundLogger

_configured = False


def configure_logging(json_logs: bool | None = None, level: str = "INFO") -> None:
    """Configure structlog process-wide. Safe to call more than once.

    Args:
        json_logs: Render JSON instead of the coloured console output. Defaults
            to the ``REFLEX_ENV_MODE=prod`` environment, so production gets
            machine-readable logs without a code change.
        level: Minimum level to emit.
    """
    global _configured
    if _configured:
        return

    if json_logs is None:
        json_logs = os.environ.get("REFLEX_ENV_MODE", "dev") == "prod"

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str) -> BoundLogger:
    """Return a logger. Bind context to it at module level in each state:

    >>> log = get_logger(__name__).bind(state="ItemsState")
    """
    configure_logging()
    return structlog.get_logger(name)
