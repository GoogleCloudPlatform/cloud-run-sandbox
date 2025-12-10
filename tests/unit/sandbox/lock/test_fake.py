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

import asyncio
import pytest
from src.sandbox.lock.fake import FakeLock
from src.sandbox.lock.interface import LockContentionError, LockTimeoutError

@pytest.fixture(autouse=True)
def clear_locks():
    FakeLock._locks.clear()

@pytest.mark.asyncio
async def test_acquire_release():
    lock = FakeLock("lock1", "owner1")
    await lock.acquire()
    assert lock._state.owner == "owner1"
    await lock.release()
    assert lock._state.owner is None

@pytest.mark.asyncio
async def test_contention():
    lock1 = FakeLock("lock1", "owner1")
    await lock1.acquire()

    lock2 = FakeLock("lock1", "owner2")
    task = asyncio.create_task(lock2.acquire())

    await asyncio.sleep(0.01)
    assert lock2._state.waiter == "owner2"

    await lock1.release()
    await task
    assert lock2._state.owner == "owner2"
    await lock2.release()

@pytest.mark.asyncio
async def test_timeout():
    lock1 = FakeLock("lock1", "owner1")
    await lock1.acquire()

    lock2 = FakeLock("lock1", "owner2")
    with pytest.raises(LockTimeoutError):
        await lock2.acquire(timeout_sec=0.1)

    await lock1.release()

@pytest.mark.asyncio
async def test_lock_contention_error():
    lock1 = FakeLock("lock1", "owner1")
    await lock1.acquire()

    lock2 = FakeLock("lock1", "owner2")
    task2 = asyncio.create_task(lock2.acquire())
    await asyncio.sleep(0.01) # let lock2 become a waiter

    lock3 = FakeLock("lock1", "owner3")
    with pytest.raises(LockContentionError):
        await lock3.acquire()

    await lock1.release()
    await task2
    await lock2.release()


@pytest.mark.asyncio
async def test_release_request_and_handoff():
    release_requested = asyncio.Event()

    async def release_handler():
        release_requested.set()

    lock1 = FakeLock("lock1", "owner1", lease_sec=1)
    lock1.on_release_requested(release_handler)
    await lock1.acquire()

    lock2 = FakeLock("lock1", "owner2")
    acquire_task = asyncio.create_task(lock2.acquire())

    await asyncio.sleep(0.1) # let lock2 become a waiter
    assert lock2._state.waiter == "owner2"

    # Trigger renewal, which should trigger release_handler
    await lock1.renew()

    await asyncio.wait_for(release_requested.wait(), timeout=1)
    assert release_requested.is_set()

    await lock1.release()
    await acquire_task
    await lock2.release()

@pytest.mark.asyncio
async def test_on_renewal_error():
    renewal_error_called = asyncio.Event()

    async def renewal_error_handler():
        renewal_error_called.set()

    lock = FakeLock("lock1", "owner1", lease_sec=0.2)
    lock.on_renewal_error(renewal_error_handler)
    await lock.acquire()

    lock.force_renewal_error()

    await asyncio.wait_for(renewal_error_called.wait(), timeout=1)
    assert renewal_error_called.is_set()

    await lock.release()

@pytest.mark.asyncio
async def test_auto_renewal_triggers_release_request():
    release_requested = asyncio.Event()

    async def release_handler():
        release_requested.set()

    lock1 = FakeLock("lock1", "owner1", lease_sec=0.2)
    lock1.on_release_requested(release_handler)
    await lock1.acquire()

    lock2 = FakeLock("lock1", "owner2")
    acquire_task = asyncio.create_task(lock2.acquire())
    await asyncio.sleep(0.01) # let lock2 become a waiter

    await asyncio.wait_for(release_requested.wait(), timeout=1)
    assert release_requested.is_set()

    await lock1.release()
    await acquire_task
    await lock2.release()

@pytest.mark.asyncio
async def test_constructor_callbacks():
    release_requested = asyncio.Event()
    renewal_error_called = asyncio.Event()

    async def release_handler():
        release_requested.set()

    async def renewal_error_handler():
        renewal_error_called.set()

    lock1 = FakeLock(
        "lock1",
        "owner1",
        lease_sec=0.2,
        on_release_requested=release_handler,
        on_renewal_error=renewal_error_handler,
    )
    await lock1.acquire()

    # Test renewal error callback
    lock1.force_renewal_error()
    await asyncio.wait_for(renewal_error_called.wait(), timeout=1)
    assert renewal_error_called.is_set()

    # Test release requested callback
    lock2 = FakeLock("lock1", "owner2")
    acquire_task = asyncio.create_task(lock2.acquire())
    await asyncio.sleep(0.01)

    await asyncio.wait_for(release_requested.wait(), timeout=1)
    assert release_requested.is_set()

    await lock1.release()
    await acquire_task
    await lock2.release()