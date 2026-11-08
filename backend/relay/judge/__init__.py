"""Challenges, duels and the headless evaluator (06 §3.2-§3.4, WP-2b).

Modules:

* ``scenario``: scenario parameters (airframe, sensors, arena, hover assist off, duration, spawns) and slots.
* ``headless``: one run on an isolated, stepped ``FleetService``; ground-truth access and the trace recorder.
* ``challenges``: the challenge set with visible and hidden seeds and the 0-100 scorers.
* ``duels``: free-for-all rounds of 2-8 programs, placements and pairwise Elo.
* ``worker``: the ``--judge-worker`` process (one per job) and the in-process runner.
* ``jobs``: the queue (at most two running), progress, cancel and cleanup.
* ``store``: ``results.json``, ``ladder.json`` and the gzip traces in ``<data_dir>/programs/``.
* ``service``: ``JudgeService`` (``FleetService.judge``), used by ``api/routes_judge.py``.

Replay trace (``GET /traces/{id}``, version 1). The data contract for the multi-aircraft replay::

    {
      "id": "tr-0123456789abcdef", "created": <wall s>, "job": "job-...", "version": 1,
      "kind": "challenge" | "duel", "challenge": "hover" (challenge traces), "round": 1 (duel traces),
      "scenario": "hover" | "duel_easy" | "duel", "seed": 1,
      "hz": 10.0, "duration_s": 26.0,
      "fields": ["x", "y", "z", "roll", "pitch", "yaw", "throttle", "hp", "flags"],
      "arena": {"radius", "floor", "ceiling"} | null,
      "course": {"id": "cage-loop", "rings": [{"c": [x, y, z], "yaw_deg", "r"}]} | null,
      "markers": [{"kind": "corner", "p": [x, y, z], "r": 0.4, "index": 1}],
      "t": [0.0, 0.1, ...],
      "aircraft": [
        {"id": "sim-1", "label": "SIM-1", "program": "hover_trim", "rev": 1, "name": "Hover trim",
         "role": "player" | "opponent" | "target" | "entrant", "team": "player",
         "spawn": {"x", "y", "yaw_deg"},
         "frames": [[x, y, z, roll, pitch, yaw, throttle, hp, flags], ...],
         "states": [[t, "climb"], [t, "hold"], ...]}
      ],
      "shots": [{"id": 7, "shooter": "sim-1", "t0": 3.2125, "p": [x, y, z], "v": [vx, vy, vz]}],
      "events": [{"t": 3.4, "kind": "hit", "vehicle": "sim-2", "shooter": "sim-1", "damage": 20, "hp": 80,
                  "point": [x, y, z]}, ...],
      "result": {...}
    }

* Frames: one row per entry of ``t`` (seconds since the programs started, 10 Hz) for every aircraft, in the
  order of ``fields``. Positions are the fleet's world ENU frame in metres (x east, y north, z up; the
  arena centre is the origin), the same frame as ``vehicle.position`` in the snapshot. ``roll`` / ``pitch``
  are radians (roll right-wing-down +, pitch nose-up +), ``yaw`` the compass heading in radians 0..2pi
  (0 north, clockwise), ``throttle`` the motor output 0..1, ``hp`` the arena hit points or null, ``flags``
  a bit set: 1 armed, 2 airborne, 4 shot down, 8 crashed, 16 out of the arena. Values are the simulator's
  ground truth, not what the program observed.
* ``states``: the program's state name (``ctx.state``) whenever it changed, sampled with the frames.
* ``shots``: every round fired, in the same frame: it leaves ``p`` at ``t0`` with velocity ``v`` and then
  follows the arena ballistics (``relay.peers.ballistics``: gravity and drag at 1/240 s), exactly as the
  ``arena.shots`` stream view describes a live round; a round ends at its hit (``events``), at the ground
  or at its time to live.
* ``events``: ``hit`` / ``down`` (arena), ``crash``, ``out`` (left the arena and was stopped), ``liftoff``,
  ``corner`` / ``ring`` / ``course_finished`` (challenge progress), ``program_faulted`` /
  ``program_stopped`` (with ``reason``).
* ``result``: the seed's ``{score, passed, metrics}`` for a challenge, the round's
  ``{winner, draw, duration_s, aircraft: [...]}`` for a duel.

Hidden seeds are never stored as traces and their seed values never leave the evaluator.
"""

from __future__ import annotations

from .headless import TRACE_FIELDS, TRACE_VERSION

__all__ = ["TRACE_FIELDS", "TRACE_VERSION"]
