"""Reusable UI pieces: dialogs, toasts, cards, tables.

Deliberately no re-exports. Components import from ``states/``, and states
import the toast helpers from here — eager re-exports in this file would make
that a circular import. Import the submodule you need directly::

    from ..components.item_dialog import item_dialog
"""
