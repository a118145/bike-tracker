"""Local FIT files as a ride source.

Two ways in, one code path: a watched drop folder for bulk backfills, and the
upload dialog on the Rides page. Both call :func:`import_bytes`, which calls
``importer.import_ride`` — the same function the Hammerhead poller will call
when it lands beside this module as ``sources/hammerhead.py``.
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session

from ...utils.logging import get_logger
from ..fit import FitParseError, name_from_filename, parse
from ..importer import ImportResult, import_ride

log = get_logger(__name__)

# Where the drop folder lives. Overridable so a container can mount it
# somewhere else without a code change.
DROP_FOLDER = Path(os.environ.get("BIKE_TRACKER_DROP_FOLDER", "data/inbox"))


@dataclass
class FileImportOutcome:
    """Per-file result of a folder scan, for reporting back to the user."""

    filename: str
    ride_id: int | None
    created: bool
    warnings: list[str]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def import_bytes(data: bytes, filename: str, session: Session) -> ImportResult:
    """Parse and import one FIT payload.

    The activity name comes from the file name — FIT activity files have no
    name field, but the Karoo encodes it as
    ``Karoo-<Name>-<date>-<time>.fit`` and masterplan §2.4.1 wants it on the
    ride card.
    """
    parsed = parse(data)
    return import_ride(
        parsed,
        session,
        name=name_from_filename(filename),
        source="file",
        external_id=filename,
    )


def import_file(path: Path, session: Session) -> FileImportOutcome:
    """Import a single ``.fit`` file, converting failures into a result row.

    A malformed file must not abort a folder scan — one corrupt download
    should cost that ride, not the other forty.
    """
    try:
        result = import_bytes(path.read_bytes(), path.name, session)
    except (FitParseError, OSError) as exc:
        log.warning("ride.import.failed", filename=path.name, error=str(exc))
        return FileImportOutcome(
            filename=path.name, ride_id=None, created=False, warnings=[], error=str(exc)
        )

    return FileImportOutcome(
        filename=path.name,
        ride_id=result.ride.id,
        created=result.created,
        warnings=result.warnings,
    )


def scan_folder(session: Session, folder: Path | None = None) -> list[FileImportOutcome]:
    """Import every ``.fit`` file in the drop folder.

    Files are left in place, not moved or deleted: re-import is already a
    no-op because rides deduplicate on the hash of the file's bytes, and
    deleting the user's only copy of a ride would be a poor trade for tidiness.
    """
    target = folder or DROP_FOLDER
    if not target.is_dir():
        log.info("ride.import.scan.no_folder", folder=str(target))
        return []

    outcomes = [import_file(path, session) for path in sorted(_fit_files(target))]
    log.info(
        "ride.import.scan",
        folder=str(target),
        files=len(outcomes),
        created=sum(1 for o in outcomes if o.created),
        failed=sum(1 for o in outcomes if not o.ok),
    )
    return outcomes


def _fit_files(folder: Path) -> Iterable[Path]:
    return (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".fit")
