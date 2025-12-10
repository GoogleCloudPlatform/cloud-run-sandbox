# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
In-memory implementation of the lock interface for testing purposes.
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional, Dict

from src.sandbox.lock.interface import (
    LockContentionError,
    LockInterface,
    LockTimeoutError,
)

logger = logging.getLogger(__name__)

class _LockState:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.owner: Optional[str] = None
        self.waiter: Optional[str] = None

class FakeLock(LockInterface):
    """
    A fake, in-memory implementation of the distributed lock that auto-renews.
    """
    _locks: Dict[str, _LockState] = {}

    def __init__(
        self,
        lock_id: str,
        owner_id: str,
        lease_sec: int = 10,
        on_release_requested: Optional[Callable[[], Awaitable[None]]] = None,
        on_renewal_error: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._lock_id = lock_id
        self._owner_id = owner_id
        self._lease_sec = lease_sec
        if self._lock_id not in self._locks:
            self._locks[self._lock_id] = _LockState()
        self._state = self._locks[self._lock_id]
        self._release_handler = on_release_requested
        self._renewal_error_handler = on_renewal_error
        self._handoff_signaled = False
        self._renew_task: Optional[asyncio.Task] = None
        self._fail_next_renewal = False

    def force_renewal_error(self):
        """Force the next renewal to fail."""
        self._fail_next_renewal = True

    async def _renew_loop(self) -> None:
        while True:
            await asyncio.sleep(self._lease_sec / 2)
            try:
                await self._renew()
            except Exception as e:
                logger.error(f"Error renewing lock {self._lock_id}: {e}")
                if self._renewal_error_handler:
                    await self._renewal_error_handler()

    async def _renew(self) -> None:
        if self._fail_next_renewal:
            self._fail_next_renewal = False
            raise RuntimeError("Forced renewal failure")

        if self._state.waiter is not None and not self._handoff_signaled:
            if self._release_handler:
                await self._release_handler()
            self._handoff_signaled = True

    async def renew(self) -> None:
        """Public method to manually trigger a renewal."""
        await self._renew()

    async def acquire(self, timeout_sec: int = 30) -> None:
        if self._state.lock.locked():
            if self._state.waiter is not None and self._state.waiter != self._owner_id:
                raise LockContentionError(
                    f"Lock {self._lock_id} is locked and has a waiter."
                )
            self._state.waiter = self._owner_id
            try:
                await asyncio.wait_for(self._state.lock.acquire(), timeout=timeout_sec)
                self._state.owner = self._owner_id
                self._state.waiter = None
            except asyncio.TimeoutError:
                if self._state.waiter == self._owner_id:
                    self._state.waiter = None
                raise LockTimeoutError(
                    f"Timeout while waiting to acquire lock {self._lock_id}"
                )
        else:
            await self._state.lock.acquire()
            self._state.owner = self._owner_id

        # Start auto-renewal
        self._renew_task = asyncio.create_task(self._renew_loop())

    async def release(self) -> None:
        if self._renew_task:
            self._renew_task.cancel()
            self._renew_task = None

        if self._state.lock.locked() and self._state.owner == self._owner_id:
            self._state.owner = None
            self._state.lock.release()
            self._handoff_signaled = False

    def on_release_requested(self, handler: Callable[[], Awaitable[None]]) -> None:
        self._release_handler = handler

    def on_renewal_error(self, handler: Callable[[], Awaitable[None]]) -> None:
        self._renewal_error_handler = handler
