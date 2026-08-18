"""Service layer for the memory subsystem — the impure half.

Everything here touches storage, the network, or the clock. The rules these
services enforce live in memory/domain, which is pure and where the tests
point.
"""
