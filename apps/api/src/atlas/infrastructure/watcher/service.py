"""The folder watcher: filesystem events → change batches (M04).

watchfiles supplies the OS-level watching and the 2 s debounce
(spine/docs-21 default); this service owns the source lifecycle — new
or removed sources are picked up on a refresh interval without
restarting the worker. It is a plain object with injected
collaborators, so tests drive it against a tmp dir and a capture
handler with no Celery or database in sight.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol
from uuid import UUID

import structlog
from watchfiles import awatch

from atlas.domain.knowledge.entities import Source

DEBOUNCE_MS = 2000

_logger = structlog.get_logger("atlas.watcher")


class WatchTargetProvider(Protocol):
    """System-level (cross-workspace) view of active folder sources.

    Deliberately NOT a repository method: repositories are user-scoped
    by the M03 contract; the watcher is a system daemon with its own
    port and a read-only query.
    """

    async def active_folder_sources(self) -> list[Source]: ...


ChangeHandler = Callable[[Source, frozenset[str]], Awaitable[None]]


class WatcherService:
    def __init__(
        self,
        targets: WatchTargetProvider,
        on_changes: ChangeHandler,
        *,
        debounce_ms: int = DEBOUNCE_MS,
        refresh_seconds: float = 10.0,
    ) -> None:
        self._targets = targets
        self._on_changes = on_changes
        self._debounce_ms = debounce_ms
        self._refresh_seconds = refresh_seconds

    async def run(self, stop: asyncio.Event) -> None:
        """Watch until ``stop`` is set; source list refreshes on an interval."""
        watchers: dict[UUID, asyncio.Task[None]] = {}
        try:
            while not stop.is_set():
                await self._reconcile(watchers, stop)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._refresh_seconds)
                except TimeoutError:
                    continue
        finally:
            for task in watchers.values():
                task.cancel()
            await asyncio.gather(*watchers.values(), return_exceptions=True)

    async def _reconcile(
        self, watchers: dict[UUID, asyncio.Task[None]], stop: asyncio.Event
    ) -> None:
        try:
            sources = await self._targets.active_folder_sources()
        except Exception:
            _logger.warning("watcher.refresh_failed", exc_info=True)
            return
        alive: dict[UUID, Source] = {}
        for source in sources:
            if await asyncio.to_thread(Path(source.uri).is_dir):  # blocking stat off-loop
                alive[source.id] = source
        for source_id, task in list(watchers.items()):
            if source_id not in alive or task.done():
                task.cancel()
                del watchers[source_id]
        for source_id, source in alive.items():
            if source_id not in watchers:
                watchers[source_id] = asyncio.create_task(
                    self._watch_source(source, stop), name=f"watch:{source.uri}"
                )
                _logger.info("watcher.source_started", source_id=str(source_id), uri=source.uri)

    async def _watch_source(self, source: Source, stop: asyncio.Event) -> None:
        root = await asyncio.to_thread(Path(source.uri).resolve)
        async for changes in awatch(root, debounce=self._debounce_ms, stop_event=stop):
            relative_paths = set()
            for _change, raw_path in changes:
                path = Path(raw_path)
                if path.is_relative_to(root):
                    relative_paths.add(path.relative_to(root).as_posix())
            if not relative_paths:
                continue
            try:
                await self._on_changes(source, frozenset(relative_paths))
            except Exception:
                # The watcher must survive handler faults; the change is
                # recoverable via reindex, and nothing fails silently —
                # this lands in the logs with the source id.
                _logger.error("watcher.handler_failed", source_id=str(source.id), exc_info=True)
