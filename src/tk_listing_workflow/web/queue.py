from __future__ import annotations

import asyncio

from ..services import FactoryTaskService


class WebTaskQueue:
    def __init__(self, task_service: FactoryTaskService) -> None:
        self.task_service = task_service
        self.max_concurrent = task_service.settings.max_concurrent
        self._active: dict[str, asyncio.Task] = {}
        self._dispatcher: asyncio.Task | None = None
        self._cleaner: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._dispatcher = asyncio.create_task(self._dispatch_loop(), name="web-task-dispatcher")
        self._cleaner = asyncio.create_task(self._cleanup_loop(), name="web-task-cleaner")

    async def stop(self) -> None:
        self._running = False
        for task in (self._dispatcher, self._cleaner):
            if task is not None:
                task.cancel()
        for task in list(self._active.values()):
            task.cancel()
        await asyncio.gather(*[task for task in (self._dispatcher, self._cleaner) if task is not None], return_exceptions=True)
        await asyncio.gather(*self._active.values(), return_exceptions=True)
        self._active.clear()

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                while self._running and len(self._active) < self.max_concurrent:
                    task_id = await asyncio.to_thread(self.task_service.claim_next_task)
                    if not task_id:
                        break
                    current_task_id = task_id
                    task = asyncio.create_task(self._run_task(current_task_id), name=f"web-task-{current_task_id}")
                    self._active[current_task_id] = task
                    task.add_done_callback(lambda _done, key=current_task_id: self._active.pop(key, None))
            except Exception:
                pass
            await asyncio.sleep(1)

    async def _run_task(self, task_id: str) -> None:
        await asyncio.to_thread(self.task_service.process_task, task_id)

    async def _cleanup_loop(self) -> None:
        interval = max(self.task_service.settings.cleanup_interval_hours * 3600, 300)
        while self._running:
            try:
                await asyncio.to_thread(self.task_service.cleanup_expired)
            except Exception:
                pass
            await asyncio.sleep(interval)
