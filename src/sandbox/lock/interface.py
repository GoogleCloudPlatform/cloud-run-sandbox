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
Defines the abstract interface for a distributed, asynchronous lock.

This module provides the `LockInterface` abstract base class, which serves as a
contract for all lock implementations within the application. This allows for
pluggable locking strategies (e.g., a real GCS-based lock or a fake in-memory
lock for testing) and promotes a clean, decoupled architecture.
"""

from abc import ABC, abstractmethod
from typing import Awaitable, Callable

class LockError(Exception):
    """Base exception for all lock-related errors."""
    pass

class LockTimeoutError(LockError):
    """Raised specifically when an attempt to acquire a lock times out."""
    pass


class LockContentionError(LockError):
    """Raised when trying to acquire a lock that already has a waiter."""
    pass


class LockInterface(ABC):
    """
    An interface for a distributed, asynchronous, advisory lock.

    Implementations of this class are responsible for managing exclusive access to a
    shared resource. They must handle their own lease renewal internally, making it
    a background process that the consumer of the lock does not need to manage.
    """

    @abstractmethod
    async def acquire(self, timeout_sec: int = 30) -> None:
        """
        Asynchronously acquires the lock, waiting if necessary.

        If the lock is held, this method attempts to become the single designated
        "waiter". If another process is already waiting, this method will fail fast.
        Upon successful acquisition, the lock will automatically renew its lease
        in the background until `release()` is called.

        Args:
            timeout_sec: The maximum number of seconds to wait for the lock after
                         becoming the designated waiter.

        Raises:
            LockContentionError: If the lock is held and another process is
                                 already waiting for it.
            LockTimeoutError: If the lock cannot be acquired within the specified
                              timeout period after becoming the waiter.
        """
        pass

    @abstractmethod
    async def release(self) -> None:
        """
        Asynchronously releases the lock.

        This method should be idempotent and must stop any background renewal tasks.
        """
        pass

    @abstractmethod
    def on_release_requested(self, handler: Callable[[], Awaitable[None]]) -> None:
        """
        Registers an async handler to be invoked when another process requests the lock.

        The provided handler is executed when `renew()` detects a handoff request,
        signaling that this process should gracefully yield the lock soon.

        Args:
            handler: A zero-argument async function that will be called to initiate
                     the graceful handoff process (e.g., checkpointing).
        """
        pass

    @abstractmethod
    def on_renewal_error(self, handler: Callable[[], Awaitable[None]]) -> None:
        """
        Registers an async handler to be invoked on a critical renewal failure.

        If an unexpected exception occurs during the renewal process, the lock
        is considered lost. The provided handler is executed to allow the
        application to react, for example by stopping protected operations.

        Args:
            handler: An async function that takes no arguments.
        """
        pass
