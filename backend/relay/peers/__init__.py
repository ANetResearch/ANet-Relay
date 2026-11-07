"""LAN arena (05 §7): peer discovery and display state, virtual bullets, hits and scores.

``wire`` is the UDP codec, ``arena`` the seat geometry, ``ballistics`` the shared bullet integrator,
``service`` the socket and the peer table, ``game`` the fleet-thread rules (fire, hits, hit points, respawn).
Nothing in this package reaches a control path: peers contribute display state and virtual bullets that only
ever touch local simulators.
"""
