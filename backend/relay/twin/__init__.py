"""Firmware twins (11 §2): official autopilot SITL builds flying Relay's physics in lockstep.

``packs`` (runtime packs), ``identity`` (firmware recognition and matching), ``params`` (the hardware
parameter filter and the parameter file writers), ``profile`` (twin profiles captured from real aircraft),
``hil`` (HIL message encoding), ``ardupilot`` / ``px4`` (process and bridge per firmware) and ``autopilot``
(``TwinAutopilot``, the object the fleet drives like a ``SimAutopilot``). The module is imported on demand;
nothing here starts a process until a twin is created.
"""
