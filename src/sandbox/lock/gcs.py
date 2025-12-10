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
Implementation of the lock interface using Google Cloud Storage.
"""
import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional
from urllib.parse import urlparse

from google.api_core import exceptions as gcs_exceptions
from google.cloud import storage

from .data import LockData
from .interface import LockContentionError, LockInterface, LockTimeoutError

logger = logging.getLogger(__name__)


class GCSLock(LockInterface):
    """
    A distributed, auto-renewing lock using Google Cloud Storage.

    **Core Mechanism**

    The lock is represented by a single JSON object in a GCS bucket, which acts
    as the source of truth. The full path to this object is what uniquely
    identifies the lock.

    The lock object contains:
    - `ownerId`: A unique ID for the process that currently holds the lock.
    - `expiresAt`: A Unix timestamp indicating when the lock lease expires.
    - `waitingOwnerId`: A unique ID for a process waiting to acquire the lock.

    **Lease and Auto-Renewal**

    CRITICAL: This lock starts a background task on `acquire()` to automatically
    renew its lease. You MUST call `release()` to stop this task, otherwise
    it will run forever and leak resources.
    """

    def __init__(
        self,
        bucket: storage.Bucket,
        blob_name: str,
        owner_id: str,
        lease_sec: int = 60,
        on_release_requested: Optional[Callable[[], Awaitable[None]]] = None,
        on_renewal_error: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        """
        Initializes the GCSLock.

        Args:
            bucket: The GCS bucket where the lock object resides.
            blob_name: The full name of the lock object within the bucket.
            owner_id: A unique identifier for the process owning the lock.
            lease_sec: The duration of the lease in seconds.
        """
        self._bucket = bucket
        self._blob_name = blob_name
        self._owner_id = owner_id
        self._lease_sec = lease_sec
        self._release_handler = on_release_requested
        self._renewal_error_handler = on_renewal_error
        self._renewal_error_signaled = False
        self._renew_task: Optional[asyncio.Task] = None
        self._waiter_check_task: Optional[asyncio.Task] = None
        self._released = False
        self._handoff_signaled = False
        self._test_only_on_renew_callback: Optional[Callable[[], None]] = None

    def _read_lock_data(self) -> Optional[LockData]:
        """
        Reads and parses the lock data from a GCS blob.

        Returns:
            A LockData object if the blob exists, otherwise None.
        """
        blob = self._bucket.blob(self._blob_name)
        try:
            # download_as_bytes() implicitly reloads metadata, so we get the
            # latest generation number.
            content = blob.download_as_bytes()
            data = json.loads(content)
            data["generation"] = blob.generation
            return LockData(raw=data)
        except gcs_exceptions.NotFound:
            # This is not an error for us. It is the expected behavior when a lock
            # does not yet exist, signaling that it is free to be acquired.
            return None

    def _write_lock_data(
        self, data: Dict[str, Any], generation: Optional[int]
    ) -> bool:
        """
        Writes lock data to a GCS blob with a generation condition.

        Args:
            generation: The generation number for optimistic concurrency control.
                        `0` means the blob must not exist (for creating a new lock).
                        A positive integer means the blob must have that exact
                        generation (for updating an existing lock).

        Returns:
            True if the write was successful, False if a precondition failed.
        """
        blob = self._bucket.blob(self._blob_name)
        try:
            blob.upload_from_string(
                json.dumps(data),
                content_type="application/json",
                if_generation_match=generation,
            )
            return True
        except gcs_exceptions.PreconditionFailed:
            # This is an expected failure during a race condition. For example,
            # if two processes try to acquire a new lock (generation=0) at the
            # same time, the first one will succeed, and the second will get this
            # exception. The caller should then retry acquiring the lock.
            return False

    @classmethod
    def from_path(
        cls,
        gcs_path: str,
        owner_id: str,
        lease_sec: int = 60,
        client: Optional[storage.Client] = None,
    ) -> "GCSLock":
        """
        Creates a GCSLock instance from a full GCS path string.

        Example:
            lock = GCSLock.from_path(
                gcs_path="gs://my-bucket/locks/sandbox-123.lock",
                owner_id="my-process-id-123",
            )

        Args:
            gcs_path: The full GCS path to the lock object.
            owner_id: A unique identifier for the process owning the lock.
            lease_sec: The duration of the lease in seconds.
            client: An optional GCS storage client.
        """
        if not client:
            client = storage.Client()

        parsed_path = urlparse(gcs_path)
        bucket_name = parsed_path.netloc
        blob_name = parsed_path.path.strip("/")

        if not bucket_name or not blob_name:
            raise ValueError(
                f'Invalid GCS path "{gcs_path}". Path must be in the format "gs://<bucket_name>/<blob_name>".'
            )

        bucket = client.bucket(bucket_name)
        return cls(
            bucket=bucket,
            blob_name=blob_name,
            owner_id=owner_id,
            lease_sec=lease_sec,
        )

    async def acquire(self, timeout_sec: int = 30) -> None:
        """
        Asynchronously acquires the lock, waiting if necessary.
        """
        lock_data = self._read_lock_data()

        if lock_data is None:
            await self._try_acquire_new_lock()
        else:
            await self._try_acquire_existing_lock(lock_data, timeout_sec)

        self._start_background_tasks()

    async def _try_acquire_new_lock(self) -> None:
        """Attempt to acquire a lock that does not currently exist."""
        new_data = {
            "ownerId": self._owner_id,
            "expiresAt": time.time() + self._lease_sec,
            "waitingOwnerId": None,
        }
        # Use generation `0` to ensure we only create a new file.
        if self._write_lock_data(new_data, generation=0):
            return  # Lock acquired

        # We lost the race to acquire the new lock.
        raise LockContentionError(
            "Failed to acquire new lock due to a race condition."
        )

    async def _try_acquire_existing_lock(
        self, lock_data: LockData, timeout_sec: int
    ) -> None:
        """Attempt to acquire a lock that already exists."""
        if lock_data.is_expired:
            await self._handle_expired_lock(lock_data)
        else:
            await self._handle_active_lock(lock_data, timeout_sec)

    async def _handle_expired_lock(
        self, lock_data: LockData
    ) -> None:
        """Handle acquiring a lock that is expired."""
        # The lock is expired. A waiter has priority over any new contender.
        if (lock_data.has_waiter and not lock_data.is_waiter_expired) and self._owner_id != lock_data.waiting_owner_id:
            raise LockContentionError(
                "Lock is expired, but an active waiter has priority."
            )

        # No active waiter, or we are the waiter. Proceed to acquire.
        new_data = {
            "ownerId": self._owner_id,
            "expiresAt": time.time() + self._lease_sec,
            "waitingOwnerId": None,
            "waiterExpiresAt": None,
        }
        generation = lock_data.generation
        if not self._write_lock_data(new_data, generation=generation):
            raise LockContentionError(
                "Failed to acquire expired lock due to a race condition."
            )

    async def _handle_active_lock(
        self, lock_data: LockData, timeout_sec: int
    ) -> None:
        """Handle acquiring a lock that is currently active."""
        # Check if there is an active, non-expired waiter.
        if lock_data.has_waiter and not lock_data.is_waiter_expired:
            raise LockContentionError(
                f"Lock is held by {lock_data.owner_id} and already has a waiter."
            )

        # The waiter slot is available (no waiter or expired waiter).
        # Try to become the new waiter.
        lock_data.raw["waitingOwnerId"] = self._owner_id
        lock_data.raw["waiterExpiresAt"] = time.time() + self._lease_sec
        generation = lock_data.generation

        if not self._write_lock_data(lock_data.raw, generation=generation):
            # We lost the race to become the waiter.
            raise LockContentionError(
                "Failed to become waiter due to a race condition."
            )

        # Successfully became the waiter. Now, poll for the lock's release.
        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout_sec:
            await asyncio.sleep(1)  # Poll interval
            logger.debug(f"Polling lock '{self._blob_name}' for release...")
            polled_lock_data = self._read_lock_data()

            if polled_lock_data is None or polled_lock_data.is_expired:
                new_data = {
                    "ownerId": self._owner_id,
                    "expiresAt": time.time() + self._lease_sec,
                    "waitingOwnerId": None,
                    "waiterExpiresAt": None,
                }
                # Use the generation from the expired lock if it exists, otherwise 0.
                generation = polled_lock_data.generation if polled_lock_data else 0
                if self._write_lock_data(new_data, generation=generation):
                    return  # Lock acquired
                else:
                    # The lock was released, but another process acquired it before us.
                    # This is likely due to eventual consistency. Instead of failing,
                    # we will just continue the polling loop to re-read the state.
                    logger.info(f"[{self._owner_id}] Lost race to claim lock. Retrying poll.")
                    continue

        # If the loop finishes, we timed out waiting for the release.
        raise LockTimeoutError(
            f"Timed out waiting for lock release by {lock_data.owner_id}."
        )

    async def _renew_loop(self) -> None:
        """The background task that periodically renews the lock lease."""
        while not self._released:
            await asyncio.sleep(self._lease_sec / 2)
            await self._renew()
            # Not need to renew as renewal error was detected.
            if self._renewal_error_signaled:
                return

    async def _waiter_check_loop(self) -> None:
        """A fast background loop to check for a new waiter."""
        while not self._released:
            await asyncio.sleep(2)  # Check every 2 seconds
            await self._check_for_waiter()
            # No need to check for waiter as handoff was already signaled.
            if self._handoff_signaled:
                return
            # No need to continue checking if renewal error was detected because the lock is lost.
            if self._renewal_error_signaled:
                return

    async def _check_for_waiter(self) -> None:
        """Checks for a waiter and triggers the handoff if needed."""
        try:
            lock_data = self._read_lock_data()
            if not lock_data or lock_data.owner_id != self._owner_id:
                return  # We don't own the lock anymore

            if lock_data.has_waiter and not lock_data.is_waiter_expired:
                if self._release_handler:
                    await self._release_handler()
                self._handoff_signaled = True
        except asyncio.CancelledError:
            # Expected cancellation when stopping the renewal loop.
            # Re-raise the exception to properly exit the task.
            raise
        except Exception:
            logger.exception(f"Error checking for waiter on lock {self._blob_name}")

    async def _renew(self) -> None:
        """Internal method to perform a single renewal operation."""
        logger.info(f"[{self._owner_id}] Renewing lock...")
        try:
            lock_data = self._read_lock_data()
            if not lock_data or lock_data.owner_id != self._owner_id:
                logger.warning(
                    f"Lock {self._blob_name} lost or expired. Stopping renewal."
                )
                # The loops will be cancelled by the release method,
                # which should be called by the owner of this lock instance.
                await self._handle_renewal_error()
                return

            lock_data.raw["expiresAt"] = time.time() + self._lease_sec
            if not self._write_lock_data(lock_data.raw, lock_data.generation):
                logger.warning(f"Failed to renew lock {self._blob_name} due to contention.")

            logger.info(f"[{self._owner_id}] Lock renewed.")
        except asyncio.CancelledError:
            # Expected cancellation when stopping the renewal loop.
            # Re-raise the exception to properly exit the task.
            raise
        except Exception as e:
            logger.exception(f"Error renewing lock {self._blob_name}, exception: {e}")
            await self._handle_renewal_error()
        finally:
            if self._test_only_on_renew_callback:
                self._test_only_on_renew_callback()
    
    async def _handle_renewal_error(self) -> None:
        if self._renewal_error_signaled:
            return
        self._renewal_error_signaled = True
        if self._renewal_error_handler:
            await self._renewal_error_handler()

    async def release(self) -> None:
        """
        Asynchronously releases the lock.

        If an active waiter is present, this method will mark the lock as expired
        to signal a handoff. Otherwise, it will delete the lock object entirely.
        """
        self._released = True
        # No need to release if renewal error was detected because the lock is lost.
        if self._renewal_error_signaled:
            return

        blob = self._bucket.blob(self._blob_name)
        try:
            lock_data = self._read_lock_data()
            if not lock_data or lock_data.owner_id != self._owner_id:
                # We are not the owner, so we shouldn't do anything.
                return

            # Check for an active waiter.
            if lock_data.has_waiter and not lock_data.is_waiter_expired:
                # There is an active waiter. Signal them by expiring the lock.
                lock_data.raw["expiresAt"] = 0
                self._write_lock_data(lock_data.raw, lock_data.generation)
            else:
                # No active waiter. Just delete the lock.
                blob.delete(if_generation_match=lock_data.generation)

        except gcs_exceptions.PreconditionFailed:
            # This is fine, it means the lock was modified between our read and
            # write/delete, so we likely don't own it anymore.
            logger.warning("Failed to release lock due to contention.")
        except gcs_exceptions.NotFound:
            # The lock was already gone, which is a valid released state.
            pass

    def on_release_requested(self, handler: Callable[[], Awaitable[None]]) -> None:
        """
        Registers an async handler to be invoked when another process requests the lock.
        """
        self._release_handler = handler

    def _start_background_tasks(self) -> None:
        """Starts the background renewal and waiter check tasks."""
        if self._renew_task and not self._renew_task.done():
            logger.warning("Renewal task is already running.")
        else:
            self._renew_task = asyncio.create_task(self._renew_loop())

        if self._waiter_check_task and not self._waiter_check_task.done():
            logger.warning("Waiter check task is already running.")
        else:
            self._waiter_check_task = asyncio.create_task(self._waiter_check_loop())


    def _test_only_register_renew_callback(self, callback: Callable[[], None]) -> None:
           """FOR TESTING: Registers a callback to be invoked after a renewal."""
           self._test_only_on_renew_callback = callback

    def on_renewal_error(self, handler: Callable[[], Awaitable[None]]) -> None:
           """
           Registers an async handler to be invoked on a critical renewal failure.
           """
           self._renewal_error_handler = handler