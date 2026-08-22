"""Ride sources.

One module per origin. Each one turns its own transport into bytes and hands
them to ``ingest.importer.import_ride``; none of them may reimplement any
part of that sequence.

* ``file`` — drop folder and upload dialog (this milestone)
* ``hammerhead`` — Karoo API poller (later; brief §3.2)
"""
