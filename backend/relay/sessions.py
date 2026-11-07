"""Agent sessions: renewable leases, per-session rate limits and call logs (03 §4, §6.1 H9).

Tokens are kept only as SHA-256 digests. The manager is thread-safe; the fleet
service expires sessions from its main loop.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import LEASE_S, RATE_CONTROL_PER_S, RATE_OTHER_PER_S, SESSION_CALL_LOG
from .errors import err

NAME_MAX = 40


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class _Bucket:
    rate: float
    tokens: float
    at: float

    def take(self, now: float) -> bool:
        self.tokens = min(self.rate, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass
class Session:
    session_id: str
    name: str
    digest: str
    created: float  # monotonic
    created_wall: float
    expires: float  # monotonic
    lease_s: float
    calls: deque = field(default_factory=lambda: deque(maxlen=SESSION_CALL_LOG))
    last_call: float | None = None  # wall
    buckets: dict[str, _Bucket] = field(default_factory=dict)

    def alive(self, now: float) -> bool:
        return self.expires > now


class SessionManager:
    def __init__(self, lease_s: float = LEASE_S, clock: Callable[[], float] = time.monotonic) -> None:
        self.lease_s = lease_s
        self.clock = clock
        self._by_digest: dict[str, Session] = {}
        self._by_id: dict[str, Session] = {}
        self._lock = threading.Lock()

    def open(self, name: str, wall: float) -> tuple[Session, str]:
        name = " ".join(str(name or "").split())[:NAME_MAX]
        if not name:
            raise err("invalid_request", reason="name is required")
        token = secrets.token_urlsafe(32)
        now = self.clock()
        session = Session(
            session_id="s-" + secrets.token_hex(4),
            name=name,
            digest=_digest(token),
            created=now,
            created_wall=wall,
            expires=now + self.lease_s,
            lease_s=self.lease_s,
        )
        with self._lock:
            self._by_digest[session.digest] = session
            self._by_id[session.session_id] = session
        return session, token

    def lookup(self, token: str | None) -> Session | None:
        if not token:
            return None
        digest = _digest(token)
        with self._lock:
            session = self._by_digest.get(digest)
        if session is None or not hmac.compare_digest(session.digest, digest):
            return None
        return session if session.alive(self.clock()) else None

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            return self._by_id.get(session_id)

    def alive(self, session_id: str | None) -> bool:
        session = self.get(session_id)
        return session is not None and session.alive(self.clock())

    def renew(self, session: Session) -> float:
        session.expires = self.clock() + session.lease_s
        return session.lease_s

    def close(self, session: Session) -> None:
        with self._lock:
            self._by_digest.pop(session.digest, None)
            self._by_id.pop(session.session_id, None)

    def expire(self) -> list[Session]:
        now = self.clock()
        with self._lock:
            gone = [s for s in self._by_id.values() if not s.alive(now)]
        for session in gone:
            self.close(session)
        return gone

    def live(self) -> list[Session]:
        now = self.clock()
        with self._lock:
            items = [s for s in self._by_id.values() if s.alive(now)]
        return sorted(items, key=lambda s: s.created)

    def allow(self, session: Session, klass: str) -> bool:
        """Token-bucket rate limit: 'control' 60/s, 'other' 20/s."""
        rate = RATE_CONTROL_PER_S if klass == "control" else RATE_OTHER_PER_S
        now = self.clock()
        bucket = session.buckets.get(klass)
        if bucket is None:
            bucket = session.buckets[klass] = _Bucket(rate=float(rate), tokens=float(rate), at=now)
        return bucket.take(now)

    def log_call(self, session: Session, wall: float, method: str, path: str, status: int, ms: float) -> None:
        session.last_call = wall
        session.calls.append(
            {"t": wall, "method": method, "path": path, "status": status, "ms": round(ms, 1)}
        )
