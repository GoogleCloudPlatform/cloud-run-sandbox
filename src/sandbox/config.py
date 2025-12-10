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

from dataclasses import dataclass
import os
from typing import Optional


@dataclass
class GCSConfig:
    """
    Configuration for GCS-backed sandbox persistence and locking.
    """
    metadata_bucket: Optional[str] = None
    metadata_mount_path: Optional[str] = None
    sandbox_checkpoint_bucket: Optional[str] = None
    sandbox_checkpoint_mount_path: Optional[str] = None
    filesystem_snapshot_bucket: Optional[str] = None
    filesystem_snapshot_mount_path: Optional[str] = None

    @classmethod
    def from_env(cls) -> "GCSConfig":
        """
        Creates a GCSConfig instance from environment variables.
        """
        return cls(
            metadata_bucket=os.environ.get("SANDBOX_METADATA_BUCKET"),
            metadata_mount_path=os.environ.get("SANDBOX_METADATA_MOUNT_PATH"),
            sandbox_checkpoint_bucket=os.environ.get("SANDBOX_CHECKPOINT_BUCKET"),
            sandbox_checkpoint_mount_path=os.environ.get("SANDBOX_CHECKPOINT_MOUNT_PATH"),
            filesystem_snapshot_bucket=os.environ.get("FILESYSTEM_SNAPSHOT_BUCKET"),
            filesystem_snapshot_mount_path=os.environ.get("FILESYSTEM_SNAPSHOT_MOUNT_PATH"),
        )

