"""Seed the two bikes from brief §2, with their ANT+ sensor ids.

Bike auto-detection cannot work until the sensor ids are in the database, so
this runs before the first import rather than being a convenience.

All three ANT+ ids below were read out of the reference FIT files in
``docs/``, which closes the "ANT+ Device ID der SRAM-Kurbel erfassen" item in
brief §7 — the SRAM crank identifies itself as 10517 in the morning ride.

Note that heart-rate strap 44050 is deliberately registered on **both** bikes:
it is one physical Decathlon strap moved between them. ``resolve_bike``
ignores sensor ids that more than one bike claims, so recording the truth here
costs nothing and stops the strap ever being read as evidence for either bike.
"""

from sqlmodel import Session, select

from ..models import Bike, BikeSensor
from ..utils.logging import get_logger

log = get_logger(__name__)

# (bike name, system weight kg, notes, [(sensor_type, ant_id, label), ...])
SEED_BIKES: list[tuple[str, float, str, list[tuple[str, int, str]]]] = [
    (
        "Canyon CF SL 7",
        90.0,
        "Road bike. SRAM Rival eTap, 48/35 x 10-30 12-speed. Single-sided power.",
        [
            ("power_meter", 10517, "SRAM crank (single-sided)"),
            ("shifting", 23828, "SRAM eTap"),
            ("heart_rate", 44050, "Decathlon chest strap"),
        ],
    ),
    (
        "Ideal Trekking",
        95.0,
        "Trekking bike, alu frame. Mechanical 3x9. Croozer trailer runs on this bike only.",
        [
            ("power_meter", 13522, "Favero Assioma Pro MX-2 (dual-sided)"),
            ("heart_rate", 44050, "Decathlon chest strap"),
        ],
    ),
]


def seed_bikes(session: Session) -> list[Bike]:
    """Create the seed bikes and sensors if they are not there already.

    Idempotent: matches on bike name and on ``(bike, ant_id)``, so running it
    again after adding a sensor by hand does not duplicate anything.
    """
    created: list[Bike] = []

    for name, weight_kg, notes, sensors in SEED_BIKES:
        bike = session.exec(select(Bike).where(Bike.name == name)).first()
        if bike is None:
            bike = Bike(name=name, weight_kg=weight_kg, notes=notes)
            session.add(bike)
            session.commit()
            session.refresh(bike)
            created.append(bike)
            log.info("seed.bike.create", bike_id=bike.id, name=name)

        for sensor_type, ant_id, label in sensors:
            exists = session.exec(
                select(BikeSensor)
                .where(BikeSensor.bike_id == bike.id)
                .where(BikeSensor.ant_id == ant_id)
            ).first()
            if exists is None:
                session.add(
                    BikeSensor(
                        bike_id=bike.id,
                        sensor_type=sensor_type,
                        ant_id=ant_id,
                        label=label,
                    )
                )
                log.info("seed.sensor.create", bike_id=bike.id, ant_id=ant_id, type=sensor_type)

    session.commit()
    return created
