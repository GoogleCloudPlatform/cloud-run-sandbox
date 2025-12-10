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

from dataclasses import dataclass, field, asdict
from typing import Optional, Callable, Type, Awaitable
import asyncio
import time
import os
import json
import logging
from functools import partial

from google.cloud import storage

from .config import GCSConfig
from .interface import SandboxCreationError, SandboxRestoreError, SandboxOperationError, SandboxNotFoundError, SandboxCheckpointError, StatusNotifier
from .lock.interface import LockInterface, LockContentionError, LockTimeoutError, LockError
from .lock.factory import LockFactory
from .verifier import CheckpointVerifier
from .types import SandboxStateEvent

logger = logging.getLogger(__name__)


async def _acquire_lock(
    lock_factory: LockFactory,
    sandbox_id: str,
    on_release_requested: Optional[Callable[[str], Awaitable[None]]] = None,
    on_renewal_error: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Optional[LockInterface]:
    """Helper to acquire a lock."""
    if not lock_factory:
        raise SandboxCreationError("Sandbox handoff is requested, but the server is not configured with a lock factory.")

    blob_name = SandboxHandle.build_lock_blob_name(sandbox_id)
    
    # Create partial functions to bind the sandbox_id to the callbacks.
    # The lock classes expect a zero-argument callable.
    release_callback = partial(on_release_requested, sandbox_id) if on_release_requested else None
    renewal_callback = partial(on_renewal_error, sandbox_id) if on_renewal_error else None

    lock = lock_factory.create_lock(
        sandbox_id,
        blob_name,
        on_release_requested=release_callback,
        on_renewal_error=renewal_callback,
    )
    try:
        await lock.acquire()
    except LockError:
        await lock.release()
        raise
    return lock


@dataclass
class GCSArtifact:
    """
    Represents a generic artifact stored in GCS.
    """
    # The GCS bucket name. Example: "my-sandbox-artifacts"
    bucket: str
    # The path to the artifact within the bucket. Example: "sandbox_checkpoints/ckpt-xyz/"
    path: str


@dataclass
class GCSSandboxMetadata:
    """
    Represents the structure of the metadata.json file in GCS. This is the persistent state.
    """
    # The unique ID of the sandbox. Example: "sandbox-abc-123"
    sandbox_id: str
    # Unix timestamp (seconds since epoch) of when the sandbox was created.
    created_timestamp: float
    # The idle timeout in seconds. Example: 300
    idle_timeout: Optional[int] = None
    # A GCSArtifact pointing to the latest checkpoint.
    latest_sandbox_checkpoint: Optional[GCSArtifact] = None
    # Whether this sandbox supports checkpointing.
    enable_sandbox_checkpoint: bool = False
    # Whether this sandbox can be handed off between different sandbox manager instances.
    enable_sandbox_handoff: bool = False
    # Whether this sandbox should be checkpointed automatically when it is idle.
    enable_idle_timeout_auto_checkpoint: bool = False


@dataclass
class SandboxHandle:
    """
    A centralized class to manage all state and configuration for a single sandbox instance.
    It holds both in-memory runtime data (like the instance and cleanup tasks) and,
    optionally, the GCS persistence configuration and loaded metadata.

    This class is also the single source of truth for constructing GCS paths,
    ensuring consistency and making path logic easily testable.
    """
    sandbox_id: str
    instance: any
    idle_timeout: int

    # In-memory runtime state
    last_activity: float = field(default_factory=time.time)
    delete_callback: Optional[Callable[[str], None]] = None
    ip_address: Optional[str] = None
    lock: Optional[LockInterface] = None

    gcs_config: Optional[GCSConfig] = None
    gcs_metadata: Optional[GCSSandboxMetadata] = None

    def __init__(
            self,
            sandbox_id: str,
            instance: any,
            idle_timeout: int,
            gcs_config: Optional[GCSConfig] = None,
            delete_callback: Optional[Callable[[str], None]] = None,
            ip_address: Optional[str] = None,
            lock: Optional[LockInterface] = None,
            status_notifier: Optional[StatusNotifier] = None,
            **kwargs):
        """
        Basic constructor for a SandboxHandle. Should not be called directly.
        Use the factory methods `create_ephemeral` or `create_persistent` instead.
        """
        self.sandbox_id = sandbox_id
        self.instance = instance
        self.idle_timeout = idle_timeout
        self.gcs_config = gcs_config
        self.delete_callback = delete_callback
        self.ip_address = ip_address
        self.last_activity = time.time()
        self.lock = lock
        self._checkpoint_id: Optional[str] = None
        self.status_notifier = status_notifier

    @classmethod
    def create_ephemeral(
        cls: Type['SandboxHandle'],
        sandbox_id: str,
        instance: any,
        idle_timeout: int,
        gcs_config: Optional[GCSConfig] = None,
        delete_callback: Optional[Callable[[str], None]] = None,
        ip_address: Optional[str] = None,
        status_notifier: Optional[StatusNotifier] = None,
        **kwargs
    ) -> 'SandboxHandle':
        """
        Factory for creating a basic, temporary, in-memory sandbox handle.

        An ephemeral handle can optionally have a `gcs_config` passed to it.
        This does not make the sandbox persistent, but it allows the handle
        to be aware of GCS mount paths for features like filesystem snapshotting,
        which can be performed on any sandbox.
        """
        return cls(sandbox_id, instance, idle_timeout, gcs_config=gcs_config, delete_callback=delete_callback, ip_address=ip_address, status_notifier=status_notifier, **kwargs)

    @classmethod
    async def create_persistent(
        cls: Type['SandboxHandle'],
        sandbox_id: str,
        instance: any,
        idle_timeout: int,
        gcs_config: GCSConfig,
        lock_factory: LockFactory,
        delete_callback: Optional[Callable[[str], None]] = None,
        ip_address: Optional[str] = None,
        enable_sandbox_handoff: bool = False,
        enable_idle_timeout_auto_checkpoint: bool = False,
        on_release_requested: Optional[Callable[[str], Awaitable[None]]] = None,
        on_renewal_error: Optional[Callable[[str], Awaitable[None]]] = None,
        status_notifier: Optional[StatusNotifier] = None,
        **kwargs
    ) -> 'SandboxHandle':
        """
        Factory method to create a new persistent (checkpointable) sandbox.

        It validates that the server is configured for checkpointing, creates
        the metadata with `enable_sandbox_checkpoint=True`, creates the GCS
        directory, and performs the initial metadata write.
        """
        # 1. Validate Capabilities
        if not gcs_config.metadata_mount_path:
            raise SandboxCreationError("Server is not configured for persistence: SANDBOX_METADATA_MOUNT_PATH is not set.")
        if not gcs_config.metadata_bucket:
            raise SandboxCreationError("Server is not configured for persistence: SANDBOX_METADATA_BUCKET is not set.")
        if not gcs_config.sandbox_checkpoint_mount_path:
            raise SandboxCreationError("Server is not configured for persistence: SANDBOX_CHECKPOINT_MOUNT_PATH is not set.")
        if not gcs_config.sandbox_checkpoint_bucket:
            raise SandboxCreationError("Server is not configured for persistence: SANDBOX_CHECKPOINT_BUCKET is not set.")

        # 2. Create Base Directory
        metadata_path = cls.build_metadata_path(gcs_config, sandbox_id)
        base_dir = os.path.dirname(metadata_path)
        try:
            os.makedirs(base_dir, exist_ok=False)
        except OSError as e:
            if os.path.exists(base_dir):
                raise SandboxCreationError(f"Sandbox directory already exists at {base_dir}")
            raise SandboxCreationError(f"Failed to create directory at {base_dir}: {e}")

        # 3. Acquire lock if handoff is enabled
        lock = None
        if enable_sandbox_handoff:
            try:
                if status_notifier:
                    await status_notifier.send_status(SandboxStateEvent.SANDBOX_LOCK_ACUIRING)
                lock = await _acquire_lock(
                    lock_factory,
                    sandbox_id,
                    on_release_requested=on_release_requested,
                    on_renewal_error=on_renewal_error,
                )
                if status_notifier:
                    await status_notifier.send_status(SandboxStateEvent.SANDBOX_LOCK_ACQUIRED)
            except LockError as e:
                raise SandboxCreationError(f"Failed to acquire lock for sandbox {sandbox_id}") from e

        # 4. Instantiate Handle
        handle = cls(sandbox_id, instance, idle_timeout, gcs_config=gcs_config, delete_callback=delete_callback, ip_address=ip_address, lock=lock, status_notifier=status_notifier, **kwargs)

        # 5. Create and Write Metadata
        handle.gcs_metadata = GCSSandboxMetadata(
            sandbox_id=sandbox_id,
            created_timestamp=time.time(),
            idle_timeout=idle_timeout,
            enable_sandbox_checkpoint=True,
            enable_sandbox_handoff=enable_sandbox_handoff,
            enable_idle_timeout_auto_checkpoint=enable_idle_timeout_auto_checkpoint,
        )
        handle.write_metadata()

        # 6. Return Handle
        return handle

    @staticmethod
    def _read_metadata_from_gcs(gcs_config: GCSConfig, sandbox_id: str) -> GCSSandboxMetadata:
        """Reads and parses the metadata.json file from the local GCS mount."""
        metadata_path = SandboxHandle.build_metadata_path(gcs_config, sandbox_id)
        try:
            with open(metadata_path, "r") as f:
                data = json.load(f)
            print(data)
            checkpoint_data = data.get("latest_sandbox_checkpoint")
            if checkpoint_data:
                data["latest_sandbox_checkpoint"] = GCSArtifact(**checkpoint_data)
            
            return GCSSandboxMetadata(**data)
        except FileNotFoundError:
            raise SandboxNotFoundError(f"Metadata file not found for sandbox {sandbox_id} at {metadata_path}")
        except (json.JSONDecodeError, TypeError) as e:
            raise SandboxRestoreError(f"Failed to parse or validate metadata for sandbox {sandbox_id}: {e}")

    @classmethod
    async def attach_persistent(
        cls: Type['SandboxHandle'],
        sandbox_id: str,
        instance: any,
        gcs_config: GCSConfig,
        lock_factory: LockFactory,
        delete_callback: Optional[Callable[[str], None]] = None,
        ip_address: Optional[str] = None,
        on_release_requested: Optional[Callable[[str], Awaitable[None]]] = None,
        on_renewal_error: Optional[Callable[[str], Awaitable[None]]] = None,
        status_notifier: Optional[StatusNotifier] = None,
        **kwargs
    ) -> 'SandboxHandle':
        """
        Factory method to create a SandboxHandle for an *existing* persistent sandbox
        by loading its metadata from GCS. This is the "attach/restore path".

        It reads the metadata.json, validates its contents against the current
        server configuration, and returns a fully initialized handle.
        """
        metadata = cls._read_metadata_from_gcs(gcs_config, sandbox_id)
        lock = None
        if metadata.enable_sandbox_handoff:
            if status_notifier:
                await status_notifier.send_status(SandboxStateEvent.SANDBOX_LOCK_ACUIRING)
            lock = await _acquire_lock(
                lock_factory,
                sandbox_id,
                on_release_requested=on_release_requested,
                on_renewal_error=on_renewal_error,
            )
            if status_notifier:
                await status_notifier.send_status(SandboxStateEvent.SANDBOX_LOCK_ACQUIRED)
            # Re-read metadata after acquiring lock to get the latest state
            metadata = cls._read_metadata_from_gcs(gcs_config, sandbox_id)

        # --- Validation ---
        if metadata.enable_sandbox_checkpoint and not gcs_config.sandbox_checkpoint_mount_path:
            raise SandboxRestoreError(f"Cannot attach to sandbox {sandbox_id}: it requires checkpointing, but the server is not configured for it.")
        
        if metadata.latest_sandbox_checkpoint:
            checkpoint = metadata.latest_sandbox_checkpoint
            if checkpoint.bucket != gcs_config.sandbox_checkpoint_bucket:
                raise SandboxRestoreError(
                    f"Mismatched checkpoint bucket for {sandbox_id}: "
                    f"metadata has '{checkpoint.bucket}', but server is configured for '{gcs_config.sandbox_checkpoint_bucket}'."
                )
        
        # Use the idle_timeout from the authoritative metadata file
        handle = cls(
            sandbox_id=sandbox_id,
            instance=instance,
            idle_timeout=metadata.idle_timeout,
            gcs_config=gcs_config,
            delete_callback=delete_callback,
            ip_address=ip_address,
            lock=lock,
            status_notifier=status_notifier,
            **kwargs
        )
        # Overwrite the gcs_metadata that the constructor might have created
        handle.gcs_metadata = metadata
        return handle

    def _ensure_directory_exists(self, dir_path: Optional[str]):
        """Helper to ensure a directory exists, creating it if it doesn't."""
        if dir_path:
            try:
                os.makedirs(dir_path, exist_ok=True)
            except OSError as e:
                raise SandboxCreationError(f"Failed to create directory at {dir_path}: {e}")

    def write_metadata(self):
        """Serializes and writes the current gcs_metadata to metadata.json."""
        if not self.gcs_metadata or not self.metadata_path:
            return

        try:
            with open(self.metadata_path, "w") as f:
                json.dump(asdict(self.gcs_metadata), f, indent=2)
        except (IOError, TypeError) as e:
            raise SandboxOperationError(f"Failed to write metadata for sandbox {self.sandbox_id}: {e}")
        
    async def release_lock(self):
        """Releases the sandbox lock, if held."""
        if self.lock:
            if self.status_notifier:
                await self.status_notifier.send_status(SandboxStateEvent.SANDBOX_LOCK_RELEASING)
            await self.lock.release()
            if self.status_notifier:
                await self.status_notifier.send_status(SandboxStateEvent.SANDBOX_LOCK_RELEASED)
            self.lock = None

    @property
    def is_sandbox_checkpointable(self) -> bool:
        """Checks if the sandbox is configured to allow checkpointing."""
        return self.gcs_metadata is not None and self.gcs_metadata.enable_sandbox_checkpoint
    
    @property
    def is_sandbox_handoff_enabled(self) -> bool:
        """Checks if the sandbox is configured to allow handoff."""
        return self.gcs_metadata is not None and self.gcs_metadata.enable_sandbox_handoff

    @property
    def is_idle_timeout_auto_checkpoint_enabled(self) -> bool:
        """Checks if the sandbox is configured to be checkpointed automatically when it is idle."""
        return self.gcs_metadata is not None and self.gcs_metadata.enable_idle_timeout_auto_checkpoint

    def update_latest_checkpoint(self):
        """
        Updates the metadata with the new latest checkpoint and persists it.
        Uses the internally generated checkpoint ID.
        """
        if not self.gcs_metadata or not self.gcs_config:
            raise SandboxOperationError("Cannot update checkpoint for a non-persistent handle.")
        if self._checkpoint_id is None:
            raise SandboxOperationError("A checkpoint ID has not been generated for this handle yet.")

        # Construct the relative path for the artifact
        relative_path = os.path.join("sandbox_checkpoints", self._checkpoint_id)
        
        self.gcs_metadata.latest_sandbox_checkpoint = GCSArtifact(
            bucket=self.gcs_config.sandbox_checkpoint_bucket,
            path=relative_path,
        )
        self.write_metadata()

    @property
    def metadata_path(self) -> Optional[str]:
        """Constructs the full local path to the metadata.json file."""
        return self.build_metadata_path(self.gcs_config, self.sandbox_id)

    @classmethod
    def build_metadata_path(cls, gcs_config: GCSConfig, sandbox_id: str) -> Optional[str]:
        """Constructs the full local path to the metadata.json file."""
        if gcs_config and gcs_config.metadata_mount_path:
            return os.path.join(gcs_config.metadata_mount_path, "sandboxes", sandbox_id, "metadata.json")
        return None

    @property
    def lock_path(self) -> Optional[str]:
        """Constructs the full local path to the lock.json file."""
        if self.gcs_config and self.gcs_config.metadata_mount_path:
            return os.path.join(self.gcs_config.metadata_mount_path, "sandboxes", self.sandbox_id, "lock.json")
        return None

    @classmethod
    def build_lock_blob_name(cls, sandbox_id: str) -> str:
        """Constructs the GCS blob name for a sandbox lock file."""
        return f"sandboxes/{sandbox_id}/lock.json"

    def sandbox_checkpoint_dir_path(self) -> Optional[str]:
        """
        Constructs the full local path for a new sandbox checkpoint directory.
        A unique ID is generated and memoized on the first call.
        Ensures the directory exists before returning.
        """
        if not (self.gcs_config and self.gcs_config.sandbox_checkpoint_mount_path):
            return None
        
        if self._checkpoint_id is None:
            self._checkpoint_id = f"ckpt_{time.time_ns()}"
        
        path = os.path.join(self.gcs_config.sandbox_checkpoint_mount_path, "sandbox_checkpoints", self._checkpoint_id)
        self._ensure_directory_exists(path)
        return path

    @classmethod
    def build_filesystem_snapshot_path(cls, gcs_config: GCSConfig, snapshot_name: str) -> Optional[str]:
        """
        Constructs the full local path for a new filesystem snapshot file.
        """
        if not gcs_config or not gcs_config.filesystem_snapshot_mount_path:
            return None
        return os.path.join(gcs_config.filesystem_snapshot_mount_path, "filesystem_snapshots", snapshot_name, f"{snapshot_name}.tar")

    def filesystem_snapshot_file_path(self, snapshot_name: str, create_parent_dir: bool = False) -> Optional[str]:
        """
        Constructs the full local path for a new filesystem snapshot file.

        Note: This method is available on all handles (ephemeral or persistent)
        that have a gcs_config. The SandboxManager is responsible for checking
        *if* the server has snapshotting enabled before calling this method.
        This method is only responsible for knowing *where* the snapshot should go.
        """
        path = self.build_filesystem_snapshot_path(self.gcs_config, snapshot_name)
        if not path:
            return None
        if create_parent_dir:
            self._ensure_directory_exists(os.path.dirname(path))
        return path

    @property
    def latest_checkpoint_path(self) -> Optional[str]:
        """
        Constructs the full local path to the latest checkpoint image.
        Assumes metadata has been validated on load.
        """
        if not self.gcs_metadata or not self.gcs_metadata.latest_sandbox_checkpoint:
            return None
        
        checkpoint = self.gcs_metadata.latest_sandbox_checkpoint
        config = self.gcs_config

        if config and config.sandbox_checkpoint_mount_path:
            return os.path.join(config.sandbox_checkpoint_mount_path, checkpoint.path)
        
        return None

    async def verify_checkpoint_persisted(self, timeout_sec: int = 30):
        """
        Verifies that all local checkpoint files have been fully uploaded to GCS.
        """
        if not self.gcs_config or not self.gcs_config.sandbox_checkpoint_bucket:
            raise SandboxOperationError("Cannot verify checkpoint persistence without GCS config.")

        local_checkpoint_path = self.sandbox_checkpoint_dir_path()
        gcs_prefix = os.path.relpath(local_checkpoint_path, self.gcs_config.sandbox_checkpoint_mount_path)

        verifier = CheckpointVerifier(gcs_client=storage.Client())
        await verifier.verify(
            local_checkpoint_path=local_checkpoint_path,
            gcs_bucket_name=self.gcs_config.sandbox_checkpoint_bucket,
            gcs_prefix=gcs_prefix,
            timeout_sec=timeout_sec,
        )

    async def delete_metadata(self):
        """Deletes the GCS metadata directory for this sandbox."""
        if not self.metadata_path:
            return

        metadata_dir = os.path.dirname(self.metadata_path)
        if os.path.exists(metadata_dir):
            try:
                import shutil
                shutil.rmtree(metadata_dir)
                logger.info(f"Deleted metadata directory for sandbox {self.sandbox_id} at {metadata_dir}")
            except OSError as e:
                logger.error(f"Failed to delete metadata directory for sandbox {self.sandbox_id} at {metadata_dir}: {e}")
                raise SandboxOperationError(f"Failed to delete metadata for sandbox {self.sandbox_id}") from e
