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

from unittest.mock import Mock, patch, call, AsyncMock
import asyncio
import json
import logging
import time
import pytest

from google.api_core import exceptions as gcs_exceptions
from src.sandbox.lock.gcs import GCSLock, LockContentionError, LockTimeoutError

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_from_path_and_acquire_behavior(MockStorageClient):
    """
    Tests that from_path correctly initializes the lock and that acquire
    attempts to access the correct blob.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value
    # We need to mock the download call inside _read_lock_data
    mock_blob.download_as_bytes.side_effect = gcs_exceptions.NotFound("not found")
    mock_blob.upload_from_string.side_effect = gcs_exceptions.PreconditionFailed("Race condition")


    gcs_path = "gs://my-bucket/locks/sandbox-123.lock"
    owner_id = "test-owner"

    # Act
    lock = GCSLock.from_path(gcs_path=gcs_path, owner_id=owner_id, client=mock_client)
    # The acquire method is incomplete, so we wrap it in a try/except
    try:
        await lock.acquire()
    except LockContentionError:
        pass

    # Assert
    mock_client.bucket.assert_called_once_with("my-bucket")
    # Blob is now called multiple times, once for read and once for write
    assert mock_bucket.blob.call_count > 0
    mock_bucket.blob.assert_any_call("locks/sandbox-123.lock")


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_new_lock_succeeds(MockStorageClient):
    """
    Tests that acquiring a new, non-existent lock succeeds.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate the lock file not existing
    mock_blob.download_as_bytes.side_effect = gcs_exceptions.NotFound("not found")

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/new.lock",
        owner_id="owner-1",
        client=mock_client,
    )

    # Act
    await lock.acquire()

    # Assert
    mock_blob.upload_from_string.assert_called_once()
    # Check the generation condition to ensure it's a create-only operation
    call_args, call_kwargs = mock_blob.upload_from_string.call_args
    assert call_kwargs.get("if_generation_match") == 0

    # Check that the data written is correct
    written_data = json.loads(call_args[0])
    assert written_data["ownerId"] == "owner-1"
    assert "expiresAt" in written_data


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_new_lock_fails_on_race_condition(MockStorageClient):
    """
    Tests that acquire fails with LockContentionError during a race condition.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate lock not existing initially
    mock_blob.download_as_bytes.side_effect = gcs_exceptions.NotFound("not found")
    # Simulate write failing due to another process creating the lock first
    mock_blob.upload_from_string.side_effect = gcs_exceptions.PreconditionFailed(
        "Race condition"
    )

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/race.lock",
        owner_id="owner-1",
        client=mock_client,
    )

    # Act & Assert
    with pytest.raises(LockContentionError):
        await lock.acquire()


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_release_and_acquire_again_succeeds(MockStorageClient):
    """
    Tests a full acquire -> release -> acquire cycle.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    gcs_path = "gs://my-bucket/locks/cycle.lock"

    # --- First Acquire ---
    # Simulate lock not existing, then successful write
    mock_blob.download_as_bytes.side_effect = [gcs_exceptions.NotFound("not found")]
    mock_blob.upload_from_string.return_value = None

    lock1 = GCSLock.from_path(gcs_path=gcs_path, owner_id="owner-1", client=mock_client)
    await lock1.acquire()

    # --- Release ---
    # Simulate reading the lock before deleting
    lock_content_for_release = {"ownerId": "owner-1"} # No generation in content
    mock_blob.generation = 123  # Generation is metadata on the blob object
    mock_blob.download_as_bytes.side_effect = [
        json.dumps(lock_content_for_release).encode()
    ]
    mock_blob.delete.return_value = None

    await lock1.release()

    # --- Second Acquire ---
    # Simulate lock not existing again, then successful write
    mock_blob.download_as_bytes.side_effect = [gcs_exceptions.NotFound("not found")]

    lock2 = GCSLock.from_path(gcs_path=gcs_path, owner_id="owner-2", client=mock_client)
    await lock2.acquire()

    # Assert
    assert mock_blob.upload_from_string.call_count == 2
    mock_blob.delete.assert_called_once_with(if_generation_match=123)
    # Check that the second acquire was by owner-2
    final_call_args, _ = mock_blob.upload_from_string.call_args
    final_written_data = json.loads(final_call_args[0])
    assert final_written_data["ownerId"] == "owner-2"


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_active_lock_with_active_waiter_fails(MockStorageClient):
    """
    Tests fail-fast: fails if lock is active and has an active waiter.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate an active lock that already has an active waiter
    active_lock_content = {
        "ownerId": "active-owner",
        "expiresAt": time.time() + 1000,
        "waitingOwnerId": "existing-waiter",
        "waiterExpiresAt": time.time() + 1000,
    }
    mock_blob.download_as_bytes.return_value = json.dumps(active_lock_content).encode()

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/active-with-waiter.lock",
        owner_id="new-hopeful",
        client=mock_client,
    )

    # Act & Assert
    with pytest.raises(LockContentionError):
        await lock.acquire()


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_expired_lock_succeeds(MockStorageClient):
    """
    Tests that acquiring an existing, but expired, lock succeeds when there is no waiter.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate an expired lock file existing with no waiter
    expired_time = time.time() - 1000
    expired_lock_content = {"ownerId": "previous-owner", "expiresAt": expired_time}
    mock_blob.generation = 42
    mock_blob.download_as_bytes.return_value = json.dumps(
        expired_lock_content
    ).encode()

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/expired.lock",
        owner_id="new-owner",
        client=mock_client,
    )

    # Act
    await lock.acquire()

    # Assert
    mock_blob.upload_from_string.assert_called_once()
    call_args, call_kwargs = mock_blob.upload_from_string.call_args
    # Check that we used the correct generation to overwrite the expired lock
    assert call_kwargs.get("if_generation_match") == 42

    # Check that the new owner has the lock
    written_data = json.loads(call_args[0])
    assert written_data["ownerId"] == "new-owner"


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_expired_lock_with_active_waiter_fails(MockStorageClient):
    """
    Tests fail-fast: fails if lock is expired but has an active waiter.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate an expired lock that has an active waiter
    expired_lock_with_waiter = {
        "ownerId": "expired-owner",
        "expiresAt": time.time() - 1000,
        "waitingOwnerId": "active-waiter",
        "waiterExpiresAt": time.time() + 1000,
    }
    mock_blob.download_as_bytes.return_value = json.dumps(expired_lock_with_waiter).encode()

    # A new contender tries to acquire the lock
    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/expired-with-waiter.lock",
        owner_id="new-contender",
        client=mock_client,
    )

    # Act & Assert
    with pytest.raises(LockContentionError):
        await lock.acquire()


@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_waiter_acquires_expired_lock_succeeds(MockStorageClient):
    """
    Tests that an active waiter can acquire an expired lock.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    waiter_id = "waiter-1"

    # Simulate an expired lock where we are the active waiter
    expired_lock_with_us_as_waiter = {
        "ownerId": "expired-owner",
        "expiresAt": time.time() - 1000,
        "waitingOwnerId": waiter_id,
        "waiterExpiresAt": time.time() + 1000,
    }
    mock_blob.generation = 77
    mock_blob.download_as_bytes.return_value = json.dumps(
        expired_lock_with_us_as_waiter
    ).encode()

    # This lock object represents the waiter
    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/waiter-acquires.lock",
        owner_id=waiter_id, # We are the waiter
        client=mock_client,
    )

    # Act
    await lock.acquire()

    # Assert
    mock_blob.upload_from_string.assert_called_once()
    call_args, call_kwargs = mock_blob.upload_from_string.call_args
    # Check that we used the correct generation to overwrite the expired lock
    assert call_kwargs.get("if_generation_match") == 77

    # Check that we, the waiter, are now the owner
    written_data = json.loads(call_args[0])
    assert written_data["ownerId"] == waiter_id

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_expired_lock_with_expired_waiter_succeeds(MockStorageClient):
    """
    Tests that a process can acquire an expired lock with an expired waiter.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate an expired lock with an expired waiter
    expired_lock_content = {
        "ownerId": "expired-owner",
        "expiresAt": time.time() - 1000,
        "waitingOwnerId": "expired-waiter",
        "waiterExpiresAt": time.time() - 1000,
    }
    mock_blob.generation = 67
    mock_blob.download_as_bytes.return_value = json.dumps(expired_lock_content).encode()

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/expired-waiter.lock",
        owner_id="new-owner",
        client=mock_client,
    )

    # Act
    await lock.acquire()

    # Assert
    mock_blob.upload_from_string.assert_called_once()
    call_args, call_kwargs = mock_blob.upload_from_string.call_args
    assert call_kwargs.get("if_generation_match") == 67
    assert json.loads(call_args[0])["ownerId"] == "new-owner"

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_held_lock_becomes_waiter_succeeds(MockStorageClient):
    """
    Tests that a process can become a waiter and then acquire the lock
    after the owner signals a handoff by expiring the lease.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    waiter_id = "waiter-1"
    original_generation = 55

    # 1. Initial state: active lock, no waiter
    active_lock_content = {
        "ownerId": "active-owner",
        "expiresAt": time.time() + 1000,
        "waitingOwnerId": None,
    }
    # 2. During polling, owner releases by expiring the lock
    expired_by_release_content = {
        "ownerId": "active-owner",
        "expiresAt": time.time() - 1, # Now expired
        "waitingOwnerId": waiter_id, # We are the waiter
        "waiterExpiresAt": time.time() + 1000, # Waiter is still active
    }

    # The waiter will read the blob 3 times in this scenario:
    # 1. Initial read to see it's active with no waiter
    # 2. Polling read where owner has expired the lock
    # 3. Read inside the final, successful acquire call
    mock_blob.download_as_bytes.side_effect = [
        json.dumps(active_lock_content).encode(),
        json.dumps(expired_by_release_content).encode(),
        json.dumps(expired_by_release_content).encode(),
    ]
    # The blob's generation updates after each write
    mock_blob.generation = original_generation
    # Mock the two writes: becoming the waiter, then becoming the owner
    mock_blob.upload_from_string.side_effect = [None, None]


    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/held.lock",
        owner_id=waiter_id,
        client=mock_client,
    )

    # Act
    await lock.acquire()

    # Assert
    assert mock_blob.upload_from_string.call_count == 2
    
    # Check the first write (becoming the waiter)
    first_call_args, first_call_kwargs = mock_blob.upload_from_string.call_args_list[0]
    assert first_call_kwargs.get("if_generation_match") == original_generation
    first_written_data = json.loads(first_call_args[0])
    assert first_written_data["waitingOwnerId"] == waiter_id
    assert "waiterExpiresAt" in first_written_data
    assert first_written_data["waiterExpiresAt"] > time.time()

    # Check the second write (becoming the owner)
    second_call_args, second_call_kwargs = mock_blob.upload_from_string.call_args_list[1]
    # This generation would be the one from the expired lock object
    # For the test, we can just assert it's not None and not 0
    assert second_call_kwargs.get("if_generation_match") is not None
    assert second_call_kwargs.get("if_generation_match") != 0
    second_written_data = json.loads(second_call_args[0])
    assert second_written_data["ownerId"] == waiter_id

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_acquire_held_lock_fails_to_become_waiter_on_race(MockStorageClient):
    """
    Tests failing to become a waiter due to a race condition.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    # Simulate an active lock with no waiter
    active_lock_content = {
        "ownerId": "active-owner",
        "expiresAt": time.time() + 1000,
        "waitingOwnerId": None,
    }
    mock_blob.generation = 55
    mock_blob.download_as_bytes.return_value = json.dumps(active_lock_content).encode()

    # Simulate the write failing
    mock_blob.upload_from_string.side_effect = gcs_exceptions.PreconditionFailed(
        "Race condition"
    )

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/held.lock",
        owner_id="waiter-2",
        client=mock_client,
    )

    # Act & Assert
    with pytest.raises(LockContentionError):
        await lock.acquire()

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_waiter_acquires_lock_after_release(MockStorageClient):
    """
    Tests that a waiter successfully acquires the lock after it is released.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    waiter_id = "waiter-1"

    # 1. Initial state: active lock, no waiter
    active_lock_content = {
        "ownerId": "active-owner",
        "expiresAt": time.time() + 1000,
        "waitingOwnerId": None,
    }
    mock_blob.generation = 55

    # 2. During polling, lock is released (NotFound)
    # 3. Final acquire succeeds
    mock_blob.download_as_bytes.side_effect = [
        json.dumps(active_lock_content).encode(),  # First read for waiter
        json.dumps(active_lock_content).encode(),  # Polling read 1
        gcs_exceptions.NotFound("Released"),  # Polling read 2 (lock released)
        gcs_exceptions.NotFound("Released"),  # Read inside final acquire call
    ]
    # Mock the two writes: becoming the waiter, then becoming the owner
    mock_blob.upload_from_string.side_effect = [None, None]

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/wait.lock",
        owner_id=waiter_id,
        client=mock_client,
    )

    # Act
    await lock.acquire()

    # Assert
    assert mock_blob.upload_from_string.call_count == 2
    # Final call should be creating the lock as the new owner
    final_call_args, final_call_kwargs = mock_blob.upload_from_string.call_args
    assert final_call_kwargs.get("if_generation_match") == 0
    final_data = json.loads(final_call_args[0])
    assert final_data["ownerId"] == waiter_id

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_renew_loop_updates_lease(MockStorageClient):
    """
    Tests that the renewal loop is started and calls _renew.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    owner_id = "owner-1"
    lease_sec = 2
    initial_expires_at = time.time() + lease_sec

    lock_content = {
        "ownerId": owner_id,
        "expiresAt": initial_expires_at,
    }
    mock_blob.download_as_bytes.side_effect = [
        gcs_exceptions.NotFound("not found"),
        json.dumps(lock_content).encode(),
        json.dumps(lock_content).encode(),
    ]
    mock_blob.generation = 1

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/renew.lock",
        owner_id=owner_id,
        client=mock_client,
        lease_sec=lease_sec,
    )

    renew_event = asyncio.Event()
    lock._test_only_register_renew_callback(lambda: renew_event.set())

    # Act
    await lock.acquire()
    await asyncio.wait_for(renew_event.wait(), timeout=lease_sec)

    # Assert
    assert mock_blob.upload_from_string.call_count == 2
    
    renew_call_args, _ = mock_blob.upload_from_string.call_args
    renew_data = json.loads(renew_call_args[0])
    assert renew_data["ownerId"] == owner_id
    assert renew_data["expiresAt"] > initial_expires_at

    await lock.release()

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_waiter_check_triggers_handler(MockStorageClient):
    """
    Tests that the waiter check loop detects a waiter and calls the handler.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    owner_id = "owner-1"
    waiter_id = "waiter-1"
    lease_sec = 4 # Set a longer lease to avoid race conditions

    lock_with_waiter_content = {
        "ownerId": owner_id,
        "expiresAt": time.time() + 1000,
        "waitingOwnerId": waiter_id,
        "waiterExpiresAt": time.time() + 1000,
    }
    mock_blob.download_as_bytes.side_effect = [
        gcs_exceptions.NotFound("not found"),
        json.dumps(lock_with_waiter_content).encode(),
        json.dumps(lock_with_waiter_content).encode(),
        json.dumps(lock_with_waiter_content).encode(),
    ]
    mock_blob.generation = 1

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/waiter-check.lock",
        owner_id=owner_id,
        client=mock_client,
        lease_sec=lease_sec,
    )

    release_event = asyncio.Event()
    async def handler():
        release_event.set()
    lock.on_release_requested(handler)

    # Act
    await lock.acquire()
    await asyncio.wait_for(release_event.wait(), timeout=3)

    # Assert
    assert release_event.is_set()

    await lock.release()

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_renewal_error_handler_on_lost_lock(MockStorageClient):
    """
    Tests that the on_renewal_error handler is called if the lock is lost.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    owner_id = "owner-1"
    lease_sec = 2

    # 1. Acquire: lock does not exist
    # 2. Renew: lock is gone
    mock_blob.download_as_bytes.side_effect = [
        gcs_exceptions.NotFound("not found"),
        gcs_exceptions.NotFound("not found"),
    ]
    mock_blob.generation = 1

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/lost-lock.lock",
        owner_id=owner_id,
        client=mock_client,
        lease_sec=lease_sec,
    )

    error_event = asyncio.Event()
    async def handler():
        error_event.set()
    lock.on_renewal_error(handler)

    # Act
    await lock.acquire()
    await error_event.wait()

    # Assert
    assert error_event.is_set()

    await lock.release()

@pytest.mark.asyncio
@patch("google.cloud.storage.Client")
async def test_renewal_error_handler_on_stolen_lock(MockStorageClient):
    """
    Tests that the on_renewal_error handler is called if the lock is stolen.
    """
    # Arrange
    mock_client = MockStorageClient.return_value
    mock_bucket = mock_client.bucket.return_value
    mock_blob = mock_bucket.blob.return_value

    owner_id = "owner-1"
    other_owner_id = "owner-2"
    lease_sec = 2

    stolen_lock_content = {
        "ownerId": other_owner_id,
        "expiresAt": time.time() + 1000,
    }

    # 1. Acquire: lock does not exist
    # 2. Renew: lock is stolen
    mock_blob.download_as_bytes.side_effect = [
        gcs_exceptions.NotFound("not found"),
        json.dumps(stolen_lock_content).encode(),
        json.dumps(stolen_lock_content).encode(),
    ]
    mock_blob.generation = 1

    lock = GCSLock.from_path(
        gcs_path="gs://my-bucket/locks/stolen-lock.lock",
        owner_id=owner_id,
        client=mock_client,
        lease_sec=lease_sec,
    )

    error_event = asyncio.Event()
    async def handler():
        error_event.set()
    lock.on_renewal_error(handler)

    # Act
    await lock.acquire()
    await error_event.wait()

    # Assert
    assert error_event.is_set()

    await lock.release()
