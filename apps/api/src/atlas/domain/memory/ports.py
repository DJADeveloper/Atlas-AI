"""Repository port for memories — workspace-scoped like everything else."""

from typing import Protocol
from uuid import UUID

from atlas.domain.memory.entities import Memory, MemoryKind


class MemoryRepository(Protocol):
    async def add(self, memory: Memory) -> None: ...
    async def save(self, memory: Memory) -> None: ...
    async def get(self, workspace_id: UUID, memory_id: UUID) -> Memory | None: ...
    async def list_active(
        self, workspace_id: UUID, *, kind: MemoryKind | None = None, limit: int = 200
    ) -> list[Memory]:
        """Live memories only: not soft-deleted, not expired."""
        ...
