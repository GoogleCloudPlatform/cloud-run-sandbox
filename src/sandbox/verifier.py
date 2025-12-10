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
import time
import logging
from google.cloud import storage

from .interface import SandboxCheckpointError

logger = logging.getLogger(__name__)


class CheckpointVerifier:
    """
    Verifies that a local checkpoint has been fully persisted to GCS.
    """

    def __init__(self, gcs_client: storage.Client):
        self._gcs_client = gcs_client

    async def verify(
        self,
        local_checkpoint_path: str,
        gcs_bucket_name: str,
        gcs_prefix: str,
        timeout_sec: int = 30,
    ):
        """
        Verifies that all local checkpoint files have been fully uploaded to GCS
        by comparing file counts and sizes. This mitigates race conditions with
        gcsfuse staging.
        """
        logger.info(f"Verifying checkpoint persistence: local='{local_checkpoint_path}', gcs='gs://{gcs_bucket_name}/{gcs_prefix}'")
        if not local_checkpoint_path or not os.path.isdir(local_checkpoint_path):
            logger.warning(f"Local checkpoint path not found or not a directory: {local_checkpoint_path}")
            return

        # 1. Get the list and sizes of local files
        local_files = {}
        for root, _, files in os.walk(local_checkpoint_path):
            for name in files:
                local_path = os.path.join(root, name)
                relative_path = os.path.relpath(local_path, local_checkpoint_path)
                local_files[relative_path] = os.path.getsize(local_path)

        if not local_files:
            logger.warning(f"No files found in local checkpoint directory: {local_checkpoint_path}")
            return

        # 2. Poll GCS until files match or timeout
        bucket = self._gcs_client.bucket(gcs_bucket_name)
        start_time = time.monotonic()

        while time.monotonic() - start_time < timeout_sec:
            remote_files = {}
            blobs = bucket.list_blobs(prefix=f"{gcs_prefix}/")
            for blob in blobs:
                relative_name = os.path.relpath(blob.name, gcs_prefix)
                # GCS can sometimes list the directory/prefix itself as an object.
                # The relative path for this is '.', so we ignore it.
                if relative_name == ".":
                    continue
                remote_files[relative_name] = blob.size

            if local_files == remote_files:
                logger.info(f"Successfully verified persistence of {len(local_files)} files for checkpoint {gcs_prefix}.")
                return

            # Detailed logging for debugging
            if len(local_files) != len(remote_files):
                logger.warning(
                    f"Checkpoint verification pending for {gcs_prefix}. "
                    f"File count mismatch. Local ({len(local_files)}): {sorted(local_files.keys())}, "
                    f"Remote ({len(remote_files)}): {sorted(remote_files.keys())}. Retrying..."
                )
            else:
                mismatched_files = {f for f in local_files if local_files.get(f) != remote_files.get(f)}
                logger.warning(
                    f"Checkpoint verification pending for {gcs_prefix}. "
                    f"File counts match, but sizes differ for {len(mismatched_files)} files. Retrying..."
                )

            await asyncio.sleep(1)

        raise SandboxCheckpointError(
            f"Timeout waiting for checkpoint {gcs_prefix} to persist in GCS. "
            f"Local file count: {len(local_files)}, Remote file count: {len(remote_files)}."
        )
