"""Shared fixtures: a fake clock, a stepped fleet service and a GCS-side driver for the simulator."""

from __future__ import annotations

import itertools
import socket
import sys
from pathlib import Path

import pytest

from relay.authority import Actor
from relay.drivers.base import MavCommands
from relay.fleet import FleetService
from relay.history import History
from relay.mav.mavlink import Parser
from relay.mav.modes import ARDUPILOT, PX4
from relay.model import StickInput
from relay.sim.autopilot import SimAutopilot

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "fakes"))


@pytest.fixture(autouse=True)
def _agent_flight_level(request, monkeypatch):
    """06 §5: sessions fly with sticks only by default. The tests written before that rule (and the ones
    that are not about it) fly sessions with every skill, so their fleets run at ``full``; tests of the
    default level are marked ``sticks_level``."""
    sticks = request.node.get_closest_marker("sticks_level")
    product = request.node.get_closest_marker("product_defaults")
    if sticks and product:
        return
    init = FleetService.__init__

    def legacy(self, *args, **kwargs):
        init(self, *args, **kwargs)
        if not sticks:
            self.settings.agent_flight_level = "full"
        if not product:
            # 12 §2.2: a new simulator is an ANetDU7 flying the PX4 flavour. The tests written before that
            # (and the ones that are not about it) add "a simulator" and mean the small ArduPilot aircraft,
            # so their fleets keep micro-120 as the default; tests of the product default are marked
            # ``product_defaults``.
            self.settings.sim_frame_default = "micro-120"

    monkeypatch.setattr(FleetService, "__init__", legacy)


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def wall(self) -> float:
        return 1.8e9 + self.t


class Stepper:
    """Drives a FleetService without its thread, advancing a fake clock in 5 ms ticks."""

    def __init__(self, svc: FleetService, clock: FakeClock) -> None:
        self.svc = svc
        self.clock = clock

    def run(self, seconds: float, renew: list | None = None) -> None:
        end = self.clock.t + seconds
        while self.clock.t < end - 1e-9:
            self.clock.t += 0.005
            for session in renew or []:
                self.svc.sessions.renew(session)
            self.svc.step(self.clock.t)

    def until(self, predicate, timeout: float, renew: list | None = None) -> bool:
        end = self.clock.t + timeout
        while self.clock.t < end:
            self.run(0.05, renew)
            if predicate():
                return True
        return False


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def svc(clock: FakeClock):
    service = FleetService(
        listen=False,
        clock=clock,
        wall=clock.wall,
        history=History(None),
        operator_token="op-token",
    )
    # WP-2a: new simulators take the typical airframe; seed them repeatably so flights replay exactly
    service.airframe_seed_source = itertools.count(1).__next__
    yield service
    service.shutdown()


@pytest.fixture
def stepper(svc: FleetService, clock: FakeClock) -> Stepper:
    return Stepper(svc, clock)


def open_agent(svc: FleetService, name: str = "agent"):
    opened = svc.open_session(name)
    session = svc.sessions.get(opened["session_id"])
    return session, Actor.session(session.session_id, session.name)


class SimPilot(MavCommands):
    """GCS-side encoder wired straight into a SimAutopilot, with simulated time."""

    def __init__(self, flavour: str = "ardupilot", **kwargs) -> None:
        super().__init__()
        self.sim = SimAutopilot(flavour, **kwargs)
        self.parser = Parser()
        self.t = 0.0
        self.msgs: list = []
        self.autopilot = ARDUPILOT if flavour == "ardupilot" else PX4
        self.sticks: StickInput | None = None
        self.sim.step(self.t)

    def _write(self, data: bytes) -> None:
        self.sim.receive(data, self.t)

    def arm(self, on: bool, force: bool = False) -> None:
        # a pilot with the transmitter on: a pilot mode arms only with stick input present (10 §1)
        if on and self.sticks is None:
            self.send_sticks(StickInput(0.0, 0.0, 0.0, 0.0))
        super().arm(on, force=force)

    def run(self, seconds: float, dt: float = 0.02) -> None:
        end = self.t + seconds
        while self.t < end - 1e-9:
            self.t += dt
            if self.sticks is not None:
                self.send_sticks(self.sticks)
            self.pace(self.t)
            self.msgs.extend(self.parser.parse(self.sim.step(self.t)))

    def texts(self) -> list[str]:
        return [m.text for m in self.msgs if m.get_type() == "STATUSTEXT"]

    @property
    def z(self) -> float:
        return self.sim.quad.s.p[2]


def free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def free_tcp_port(host: str = "127.0.0.1") -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
