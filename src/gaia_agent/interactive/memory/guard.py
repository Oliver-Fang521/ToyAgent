import asyncio

from gaia_agent.reliability import InvocationDeadline


class MemoryRevoked(asyncio.CancelledError):
    """Old account data must never authorize another external invocation."""


class MemoryLease(InvocationDeadline):
    def __init__(self, snapshot, repository, deadline, clock):
        super().__init__(deadline, clock)
        self.snapshot = snapshot
        self.repository = repository

    def ensure_open(self):
        if (
            self._closed
            or self.repository.settings(self.snapshot.owner).generation != self.snapshot.generation
        ):
            raise MemoryRevoked("memory_revoked")
        super().ensure_open()
