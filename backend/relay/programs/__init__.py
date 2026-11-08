"""Onboard programs (06 §2, WP-1): restricted Python that maps observations to sticks at 50 Hz.

This package docstring is the stable program API; WP-2 (judge), WP-3 (Lab) and WP-4b (Copilot tools) build
on it.

Program source::

    PARAMS = {"target_alt": 1.2}            # optional literal dict: numbers, booleans, strings <= 32 chars
    PARAM_RANGES = {"target_alt": [0.3, 3]}  # optional literal {name: [low, high]} for numeric parameters

    def init(ctx):                           # optional, once at deployment (100 ms limit)
        ctx.state = "climb"

    def step(obs, ctx):                      # required, every 20 ms; returns Sticks
        return Sticks(throttle=0.5)

* ``Sticks(roll=0, pitch=0, yaw=0, throttle=0, fire=False)``: roll / pitch / yaw in [-1, 1] (right, forward,
  clockwise positive), throttle in [0, 1]; out-of-range values are clamped, non-finite values fail the frame.
* ``ctx``: ``mem`` (dict kept across frames), ``params`` (read-only, live), ``state`` (str <= 32, shown on the
  stage), ``log(*args)`` (<= 4 lines per frame; ``print`` does the same), ``t`` (s since deployment), ``rng``
  (``random() uniform() gauss() randint() choice()``, seeded at deployment).
* ``obs`` (read-only; attribute or item access; ``None`` where the aircraft has no such sensor):
  ``t dt armed airborne mode roll pitch yaw`` (deg, yaw 0-360) ``p q r`` (deg/s, body rates)
  ``ax ay az`` (m/s², specific force in the body frame forward / right / up: about +9.81 on ``az`` at rest)
  ``alt vz`` (m, m/s, barometric, relative to the take-off point) ``pos vel`` (ENU tuples, m and m/s)
  ``battery`` (``v_cell``, ``pct``) ``throttle_out`` (0-1) ``hp ammo reloading`` (arena) ``contacts``
  (tuple of ``{id, team, dist, bearing, elevation, rel, vrel, hp}``; ``rel``/``vrel`` are (forward, right, up)
  in the heading frame, bearing positive to the right, 30 m range, at most 16, noisy) ``arena``
  (``radius floor ceiling``) ``sticks_in`` (the person's sticks in copilot mode).
* Library (no import needed; also as ``relay.<name>``): ``clamp(x, lo, hi)``, ``wrap180(deg)``,
  ``lerp(a, b, t)``, ``deadband(x, d)``, ``lowpass(prev, x, alpha)``, ``PID(kp, ki, kd, i_limit, out_limit)``
  with ``step(error, dt)`` / ``reset()``, ``StateMachine(initial)`` with ``add(name, fn)`` /
  ``step(obs, ctx)`` (``fn(obs, ctx) -> (Sticks, next_state | None)``), ``Timer.start/stop/running/elapsed/
  done(ctx, name, ...)`` (in ``ctx.mem["timers"]``).
* Language: see ``validator.py`` (AST whitelist, ``import math`` only, no names or attributes with ``_``).
* A frame that raises or returns no ``Sticks`` sends safe sticks (attitude centred, throttle held) and
  counts a fault; 25 consecutive faults (0.5 s) fault the program and hand control back to the safety layer.

Modules: ``validator`` (rules), ``lib`` (runtime library), ``host`` (program-host process and ``Engine``),
``hostclient`` (backend side of the pipe), ``obs`` (observation builder), ``store`` (programs on disk),
``service`` (deployment on the fleet thread).
"""
