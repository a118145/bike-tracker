"""App entry point.

Four jobs only: configure logging, import the models so the schema is
complete, import the page modules so their ``@rx.page`` decorators register,
and hang the Hammerhead poller off the backend lifecycle. Routes themselves
are declared next to the pages, and their paths come from
``navigation/routes.py``.
"""

import reflex as rx

# Reflex only sees models the app actually imports. A table written solely by
# the importer and never rendered by a page would otherwise be missed by
# `reflex db makemigrations` and only surface as schema drift (brief §3.1).
from . import models, pages
from .ingest.scheduler import poller_lifespan
from .utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

app = rx.App()

# The poll timer, tied to the process rather than to a browser session. An
# `@rx.event(background=True)` would stop when the last tab closes and run
# three times when three are open (AGENTS.md rule 14, brief §3.2).
app.register_lifespan_task(poller_lifespan)

log.info("app.start", pages=len(pages.__all__), models=len(models.__all__))
