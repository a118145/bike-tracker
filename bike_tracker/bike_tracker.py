"""App entry point.

Three jobs only: configure logging, import the models so the schema is
complete, and import the page modules so their ``@rx.page`` decorators
register. Routes themselves are declared next to the pages, and their paths
come from ``navigation/routes.py``.
"""

import reflex as rx

# Reflex only sees models the app actually imports. A table written solely by
# the importer and never rendered by a page would otherwise be missed by
# `reflex db makemigrations` and only surface as schema drift (brief §3.1).
from . import models, pages
from .utils.logging import configure_logging, get_logger

configure_logging()
log = get_logger(__name__)

app = rx.App()

log.info("app.start", pages=len(pages.__all__), models=len(models.__all__))
