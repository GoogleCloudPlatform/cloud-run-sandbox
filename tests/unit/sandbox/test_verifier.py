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
import os
from unittest.mock import MagicMock, patch

import pytest
from google.cloud import storage

from src.sandbox.verifier import CheckpointVerifier
from src.sandbox.interface import SandboxCheckpointError


class MockBlob:
    def __init__(self, name, size):
        self.name = name
        self.size = size


@pytest.fixture
def mock_gcs_client():
    """Creates a mock GCS client with a mock bucket."""
    mock_client = MagicMock(spec=storage.Client)
    mock_bucket = MagicMock(spec=storage.Bucket)
    mock_client.bucket.return_value = mock_bucket
    return mock_client


@pytest.fixture
def verifier(mock_gcs_client):
    """Creates a CheckpointVerifier instance with a mock GCS client."""
    return CheckpointVerifier(gcs_client=mock_gcs_client)


def create_local_files(base_path, files_to_create):
    """Helper to create a directory structure with files."""
    for file_path, content in files_to_create.items():
        full_path = os.path.join(base_path, file_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)


@pytest.mark.asyncio
async def test_verify_success_on_first_try(verifier, mock_gcs_client, tmp_path):
    """
    Tests that verification succeeds immediately if remote files match local files.
    """
    local_dir = tmp_path / "checkpoint"
    local_dir.mkdir()
    create_local_files(local_dir, {"file1.txt": "hello", "subdir/file2.txt": "world"})

    mock_bucket = mock_gcs_client.bucket.return_value
    mock_bucket.list_blobs.return_value = [
        MockBlob(name="gcs/prefix/file1.txt", size=5),
        MockBlob(name="gcs/prefix/subdir/file2.txt", size=5),
    ]

    await verifier.verify(str(local_dir), "test-bucket", "gcs/prefix")

    mock_bucket.list_blobs.assert_called_once_with(prefix="gcs/prefix/")


@pytest.mark.asyncio
async def test_verify_success_after_polling(verifier, mock_gcs_client, tmp_path):
    """
    Tests that verification succeeds after a few polling attempts.
    """
    local_dir = tmp_path / "checkpoint"
    local_dir.mkdir()
    create_local_files(local_dir, {"file1.txt": "hello"})

    mock_bucket = mock_gcs_client.bucket.return_value
    # Simulate GCS being initially out of sync
    mock_bucket.list_blobs.side_effect = [
        [],  # First call: empty
        [MockBlob(name="gcs/prefix/file1.txt", size=4)],  # Second call: wrong size
        [MockBlob(name="gcs/prefix/file1.txt", size=5)],  # Third call: correct
    ]

    await verifier.verify(str(local_dir), "test-bucket", "gcs/prefix")

    assert mock_bucket.list_blobs.call_count == 3


@pytest.mark.asyncio
async def test_verify_timeout_on_file_count_mismatch(verifier, mock_gcs_client, tmp_path):
    """
    Tests that verification times out if the remote file count never matches.
    """
    local_dir = tmp_path / "checkpoint"
    local_dir.mkdir()
    create_local_files(local_dir, {"file1.txt": "hello", "file2.txt": "world"})

    mock_bucket = mock_gcs_client.bucket.return_value
    mock_bucket.list_blobs.return_value = [
        MockBlob(name="gcs/prefix/file1.txt", size=5),
    ]

    with pytest.raises(SandboxCheckpointError, match="Timeout waiting for checkpoint"):
        await verifier.verify(str(local_dir), "test-bucket", "gcs/prefix", timeout_sec=0.1)


@pytest.mark.asyncio
async def test_verify_timeout_on_file_size_mismatch(verifier, mock_gcs_client, tmp_path):
    """
    Tests that verification times out if a remote file size never matches.
    """
    local_dir = tmp_path / "checkpoint"
    local_dir.mkdir()
    create_local_files(local_dir, {"file1.txt": "hello"})

    mock_bucket = mock_gcs_client.bucket.return_value
    mock_bucket.list_blobs.return_value = [
        MockBlob(name="gcs/prefix/file1.txt", size=999),  # Consistently wrong size
    ]

    with pytest.raises(SandboxCheckpointError, match="Timeout waiting for checkpoint"):
        await verifier.verify(str(local_dir), "test-bucket", "gcs/prefix", timeout_sec=0.1)


@pytest.mark.asyncio
async def test_verify_empty_local_directory(verifier, mock_gcs_client, tmp_path):
    """
    Tests that verification completes successfully for an empty local directory.
    """
    local_dir = tmp_path / "checkpoint"
    local_dir.mkdir()

    await verifier.verify(str(local_dir), "test-bucket", "gcs/prefix")

    mock_gcs_client.bucket.return_value.list_blobs.assert_not_called()


@pytest.mark.asyncio
async def test_verify_nonexistent_local_directory(verifier, mock_gcs_client, tmp_path):
    """
    Tests that verification completes successfully if the local directory doesn't exist.
    """
    local_dir = tmp_path / "nonexistent"

    await verifier.verify(str(local_dir), "test-bucket", "gcs/prefix")

    mock_gcs_client.bucket.return_value.list_blobs.assert_not_called()
