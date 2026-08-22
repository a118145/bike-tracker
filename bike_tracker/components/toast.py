"""Toast helpers.

States call ``notify_success(...)`` instead of ``rx.toast.success(...)`` so
duration and position stay consistent in one place. Change the defaults here
and every toast in the app follows.

There is no provider to mount: ``rx.App`` already defaults ``toaster`` to
``rx.toast.provider()``. Adding another one produces duplicate toasts.
"""

import reflex as rx
from reflex.event import EventSpec

_DEFAULTS: dict[str, object] = {
    "position": "top-right",
    "duration": 4000,
    "close_button": True,
}


def notify_success(message: str, **props: object) -> EventSpec:
    return rx.toast.success(message, **{**_DEFAULTS, **props})


def notify_error(message: str, **props: object) -> EventSpec:
    """Errors stay up longer — they are usually actionable."""
    return rx.toast.error(message, **{**_DEFAULTS, "duration": 7000, **props})


def notify_warning(message: str, **props: object) -> EventSpec:
    return rx.toast.warning(message, **{**_DEFAULTS, **props})


def notify_info(message: str, **props: object) -> EventSpec:
    return rx.toast.info(message, **{**_DEFAULTS, **props})
