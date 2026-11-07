"""Bench procedures (05 §4): calibration and stability tests orchestrated above plans.

``catalogue`` lists the procedures and steps, ``steps`` implements S1 to S12, ``runner`` is the state machine,
``analysis`` and ``metrics`` measure, ``suggestions`` holds the recommended ranges, ``report`` exports and
``service`` mixes the API into ``FleetService``.
"""

from .service import ProcedureOps

__all__ = ["ProcedureOps"]
