"""Alembic head frozen at historical packet delivery (pre forward-head advances).

Packets delivered when the global Alembic head was ``a360s010004`` must keep
that delivery-time pin without comparing the live ``EXPECTED_ALEMBIC_HEAD``.
"""

A360S010004_AT_PACKET_DELIVERY = "a360s010004"
