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

import unittest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
import json
import tempfile
import os
import time
import asyncio
from functools import partial

from src.sandbox.handle import SandboxHandle, GCSArtifact, GCSSandboxMetadata, _acquire_lock
from src.sandbox.config import GCSConfig
from src.sandbox.interface import SandboxCreationError, SandboxRestoreError
from src.sandbox.lock.factory import LockFactory
from src.sandbox.lock.interface import LockContentionError


class TestSandboxHandleFactories(unittest.TestCase):
    def test_create_ephemeral_handle(self):
        """
        Tests the `create_ephemeral` factory for a basic, in-memory handle.
        """
        mock_instance = MagicMock()
        
        handle = SandboxHandle.create_ephemeral(
            sandbox_id="ephemeral-sandbox",
            instance=mock_instance,
            idle_timeout=120,
        )

        self.assertEqual(handle.sandbox_id, "ephemeral-sandbox")
        self.assertIs(handle.instance, mock_instance)
        self.assertEqual(handle.idle_timeout, 120)
        self.assertIsNone(handle.gcs_config)
        self.assertIsNone(handle.gcs_metadata)

    def test_create_ephemeral_handle_with_gcs_config_for_snapshotting(self):
        """
        Tests that an ephemeral handle can be created with a GCSConfig for snapshotting.
        """
        mock_instance = MagicMock()
        gcs_config = GCSConfig(filesystem_snapshot_mount_path="/mnt/snapshots")

        handle = SandboxHandle.create_ephemeral(
            sandbox_id="snapshot-sandbox",
            instance=mock_instance,
            idle_timeout=120,
            gcs_config=gcs_config,
        )

        self.assertIs(handle.gcs_config, gcs_config)
        self.assertIsNone(handle.gcs_metadata) # Still not persistent
        self.assertEqual(
            handle.filesystem_snapshot_file_path("my-snapshot"),
            "/mnt/snapshots/filesystem_snapshots/my-snapshot/my-snapshot.tar"
        )

    def test_create_persistent_fails_if_no_metadata_path(self):
        mock_instance = MagicMock()
        gcs_config = GCSConfig(metadata_bucket="b", sandbox_checkpoint_mount_path="c", sandbox_checkpoint_bucket="d")
        with self.assertRaises(SandboxCreationError):
            asyncio.run(SandboxHandle.create_persistent("id", mock_instance, 300, gcs_config, MagicMock()))

    def test_create_persistent_fails_if_no_metadata_bucket(self):
        mock_instance = MagicMock()
        gcs_config = GCSConfig(metadata_mount_path="a", sandbox_checkpoint_mount_path="c", sandbox_checkpoint_bucket="d")
        with self.assertRaises(SandboxCreationError):
            asyncio.run(SandboxHandle.create_persistent("id", mock_instance, 300, gcs_config, MagicMock()))

    def test_create_persistent_fails_if_no_checkpoint_path(self):
        mock_instance = MagicMock()
        gcs_config = GCSConfig(metadata_mount_path="a", metadata_bucket="b", sandbox_checkpoint_bucket="d")
        with self.assertRaises(SandboxCreationError):
            asyncio.run(SandboxHandle.create_persistent("id", mock_instance, 300, gcs_config, MagicMock()))

    def test_create_persistent_fails_if_no_checkpoint_bucket(self):
        mock_instance = MagicMock()
        gcs_config = GCSConfig(metadata_mount_path="a", metadata_bucket="b", sandbox_checkpoint_mount_path="c")
        with self.assertRaises(SandboxCreationError):
            asyncio.run(SandboxHandle.create_persistent("id", mock_instance, 300, gcs_config, MagicMock()))


class TestAsyncSandboxHandleFactories(unittest.IsolatedAsyncioTestCase):
    async def test_create_persistent_handle_success_with_temp_dir(self):
        """
        Tests the `create_persistent` factory using a real temporary directory
        to verify file and directory creation.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_instance = MagicMock()
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            handle = await SandboxHandle.create_persistent(
                sandbox_id="persistent-sandbox",
                instance=mock_instance,
                idle_timeout=300,
                gcs_config=gcs_config,
                lock_factory=MagicMock(),
            )

            # Verify the metadata file was created and has the correct content
            expected_path = os.path.join(temp_dir, "sandboxes", "persistent-sandbox", "metadata.json")
            self.assertTrue(os.path.exists(expected_path))

            with open(expected_path, "r") as f:
                metadata = json.load(f)
            
            self.assertEqual(metadata["sandbox_id"], "persistent-sandbox")
            self.assertEqual(metadata["idle_timeout"], 300)
            self.assertTrue(metadata["enable_sandbox_checkpoint"])
            self.assertFalse(metadata["enable_sandbox_handoff"])
            self.assertFalse(metadata["enable_idle_timeout_auto_checkpoint"])

    async def test_create_persistent_handle_with_auto_checkpoint(self):
        """
        Tests that `enable_idle_timeout_auto_checkpoint` is persisted correctly.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_instance = MagicMock()
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            handle = await SandboxHandle.create_persistent(
                sandbox_id="auto-checkpoint-sandbox",
                instance=mock_instance,
                idle_timeout=300,
                gcs_config=gcs_config,
                lock_factory=MagicMock(),
                enable_idle_timeout_auto_checkpoint=True,
            )

            expected_path = os.path.join(temp_dir, "sandboxes", "auto-checkpoint-sandbox", "metadata.json")
            self.assertTrue(os.path.exists(expected_path))

            with open(expected_path, "r") as f:
                metadata = json.load(f)
            
            self.assertTrue(metadata["enable_idle_timeout_auto_checkpoint"])
            self.assertTrue(handle.is_idle_timeout_auto_checkpoint_enabled)

    async def test_create_persistent_fails_if_directory_exists(self):
        """
        Tests that `create_persistent` fails if the sandbox directory already exists.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Manually create the directory that the handle is supposed to create
            os.makedirs(os.path.join(temp_dir, "sandboxes", "existing-sandbox"))

            mock_instance = MagicMock()
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            with self.assertRaises(SandboxCreationError):
                await SandboxHandle.create_persistent(
                    sandbox_id="existing-sandbox",
                    instance=mock_instance,
                    idle_timeout=300,
                    gcs_config=gcs_config,
                    lock_factory=MagicMock(),
                )

    async def test_create_persistent_with_handoff_acquires_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_lock = AsyncMock()
            mock_lock_factory = MagicMock(spec=LockFactory)
            mock_lock_factory.create_lock.return_value = mock_lock

            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            handle = await SandboxHandle.create_persistent(
                sandbox_id="locked-sandbox",
                instance=MagicMock(),
                idle_timeout=300,
                gcs_config=gcs_config,
                lock_factory=mock_lock_factory,
                enable_sandbox_handoff=True,
            )

            mock_lock_factory.create_lock.assert_called_once_with(
                "locked-sandbox",
                "sandboxes/locked-sandbox/lock.json",
                on_release_requested=None,
                on_renewal_error=None,
            )
            mock_lock.acquire.assert_awaited_once()
            self.assertIs(handle.lock, mock_lock)

    async def test_create_persistent_lock_contention_raises_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_lock = AsyncMock()
            mock_lock.acquire.side_effect = LockContentionError("already locked")
            mock_lock_factory = MagicMock(spec=LockFactory)
            mock_lock_factory.create_lock.return_value = mock_lock

            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            with self.assertRaises(SandboxCreationError):
                await SandboxHandle.create_persistent(
                    sandbox_id="contended-sandbox",
                    instance=MagicMock(),
                    idle_timeout=300,
                    gcs_config=gcs_config,
                    lock_factory=mock_lock_factory,
                    enable_sandbox_handoff=True,
                )

    @patch('src.sandbox.handle._acquire_lock')
    async def test_create_persistent_passes_callbacks(self, mock_acquire_lock):
        with tempfile.TemporaryDirectory() as temp_dir:
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )
            on_release = AsyncMock()
            on_error = AsyncMock()

            await SandboxHandle.create_persistent(
                sandbox_id="callback-sandbox",
                instance=MagicMock(),
                idle_timeout=300,
                gcs_config=gcs_config,
                lock_factory=MagicMock(),
                enable_sandbox_handoff=True,
                on_release_requested=on_release,
                on_renewal_error=on_error,
            )

            mock_acquire_lock.assert_awaited_once_with(
                ANY,
                "callback-sandbox",
                on_release_requested=on_release,
                on_renewal_error=on_error,
            )

    @patch('src.sandbox.handle._acquire_lock')
    async def test_attach_persistent_passes_callbacks(self, mock_acquire_lock):
        with tempfile.TemporaryDirectory() as temp_dir:
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )
            on_release = AsyncMock()
            on_error = AsyncMock()

            # Create a dummy metadata file
            sandbox_id = "attach-callback-sandbox"
            metadata_dir = os.path.join(temp_dir, "sandboxes", sandbox_id)
            os.makedirs(metadata_dir)
            with open(os.path.join(metadata_dir, "metadata.json"), "w") as f:
                json.dump({
                    "sandbox_id": sandbox_id,
                    "created_timestamp": time.time(),
                    "idle_timeout": 300,
                    "enable_sandbox_checkpoint": True,
                    "enable_sandbox_handoff": True,
                }, f)

            await SandboxHandle.attach_persistent(
                sandbox_id=sandbox_id,
                instance=MagicMock(),
                gcs_config=gcs_config,
                lock_factory=MagicMock(),
                on_release_requested=on_release,
                on_renewal_error=on_error,
            )

            mock_acquire_lock.assert_awaited_once_with(
                ANY,
                sandbox_id,
                on_release_requested=on_release,
                on_renewal_error=on_error,
            )


class TestAttachPersistentFactory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mock_instance = MagicMock()
        self.sandbox_id = "existing-sandbox"
        
        self.gcs_config = GCSConfig(
            metadata_mount_path=self.temp_dir.name,
            metadata_bucket="my-metadata-bucket",
            sandbox_checkpoint_mount_path=self.temp_dir.name,
            sandbox_checkpoint_bucket="my-checkpoints-bucket",
        )

        self.sandbox_dir = os.path.join(self.temp_dir.name, "sandboxes", self.sandbox_id)
        os.makedirs(self.sandbox_dir)
        self.metadata_path = os.path.join(self.sandbox_dir, "metadata.json")
        
        self.valid_metadata = {
            "sandbox_id": self.sandbox_id,
            "created_timestamp": time.time(),
            "idle_timeout": 450,
            "enable_sandbox_checkpoint": True,
            "enable_sandbox_handoff": False,
            "enable_idle_timeout_auto_checkpoint": True,
            "latest_sandbox_checkpoint": {
                "bucket": "my-checkpoints-bucket",
                "path": "sandbox_checkpoints/ckpt-xyz/"
            }
        }
        with open(self.metadata_path, "w") as f:
            json.dump(self.valid_metadata, f)

    def tearDown(self):
        self.temp_dir.cleanup()

    async def test_attach_persistent_success(self):
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock_factory.create_lock.return_value = AsyncMock()
        handle = await SandboxHandle.attach_persistent(
            sandbox_id=self.sandbox_id,
            instance=self.mock_instance,
            gcs_config=self.gcs_config,
            lock_factory=mock_lock_factory,
        )

        self.assertIsNotNone(handle.gcs_metadata)
        self.assertEqual(handle.idle_timeout, 450)
        self.assertTrue(handle.is_idle_timeout_auto_checkpoint_enabled)
        self.assertIsNone(handle.lock)

    async def test_attach_persistent_with_handoff_acquires_lock(self):
        self.valid_metadata["enable_sandbox_handoff"] = True
        with open(self.metadata_path, "w") as f:
            json.dump(self.valid_metadata, f)

        mock_lock = AsyncMock()
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock_factory.create_lock.return_value = mock_lock

        handle = await SandboxHandle.attach_persistent(
            sandbox_id=self.sandbox_id,
            instance=self.mock_instance,
            gcs_config=self.gcs_config,
            lock_factory=mock_lock_factory,
        )

        mock_lock_factory.create_lock.assert_called_once_with(
            self.sandbox_id,
            f"sandboxes/{self.sandbox_id}/lock.json",
            on_release_requested=None,
            on_renewal_error=None,
        )
        mock_lock.acquire.assert_awaited_once()
        self.assertIs(handle.lock, mock_lock)

    async def test_attach_persistent_fails_if_metadata_not_found(self):
        os.remove(self.metadata_path)
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock_factory.create_lock.return_value = AsyncMock()
        with self.assertRaises(SandboxRestoreError):
            await SandboxHandle.attach_persistent(
                sandbox_id=self.sandbox_id,
                instance=self.mock_instance,
                gcs_config=self.gcs_config,
                lock_factory=mock_lock_factory,
            )

    async def test_attach_persistent_fails_if_server_lacks_capabilities(self):
        invalid_gcs_config = GCSConfig(
            metadata_mount_path=self.temp_dir.name,
            metadata_bucket="my-metadata-bucket",
        )
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock_factory.create_lock.return_value = AsyncMock()
        with self.assertRaisesRegex(SandboxRestoreError, "requires checkpointing"):
            await SandboxHandle.attach_persistent(
                sandbox_id=self.sandbox_id,
                instance=self.mock_instance,
                gcs_config=invalid_gcs_config,
                lock_factory=mock_lock_factory,
            )

    async def test_attach_persistent_fails_on_bucket_mismatch(self):
        mismatched_gcs_config = GCSConfig(
            metadata_mount_path=self.temp_dir.name,
            metadata_bucket="my-metadata-bucket",
            sandbox_checkpoint_mount_path=self.temp_dir.name,
            sandbox_checkpoint_bucket="a-DIFFERENT-bucket-name",
        )
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock_factory.create_lock.return_value = AsyncMock()
        with self.assertRaisesRegex(SandboxRestoreError, "Mismatched checkpoint bucket"):
            await SandboxHandle.attach_persistent(
                sandbox_id=self.sandbox_id,
                instance=self.mock_instance,
                gcs_config=mismatched_gcs_config,
                lock_factory=mock_lock_factory,
            )


    async def test_attach_persistent_lock_contention_raises_error(self):
        self.valid_metadata["enable_sandbox_handoff"] = True
        with open(self.metadata_path, "w") as f:
            json.dump(self.valid_metadata, f)

        mock_lock = AsyncMock()
        mock_lock.acquire.side_effect = LockContentionError("already locked")
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock_factory.create_lock.return_value = mock_lock

        with self.assertRaises(LockContentionError):
            await SandboxHandle.attach_persistent(
                sandbox_id=self.sandbox_id,
                instance=self.mock_instance,
                gcs_config=self.gcs_config,
                lock_factory=mock_lock_factory,
            )


class TestSandboxHandleMethods(unittest.IsolatedAsyncioTestCase):
    async def test_update_latest_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_instance = MagicMock()
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            handle = await SandboxHandle.create_persistent(
                sandbox_id="persistent-sandbox",
                instance=mock_instance,
                idle_timeout=300,
                gcs_config=gcs_config,
                lock_factory=MagicMock(),
            )

            checkpoint_path = handle.sandbox_checkpoint_dir_path()
            self.assertIsNotNone(checkpoint_path)
            checkpoint_id = os.path.basename(checkpoint_path)

            handle.update_latest_checkpoint()

            metadata_path = os.path.join(temp_dir, "sandboxes", "persistent-sandbox", "metadata.json")
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

            self.assertIn("latest_sandbox_checkpoint", metadata)
            checkpoint_info = metadata["latest_sandbox_checkpoint"]
            self.assertEqual(checkpoint_info["bucket"], "my-checkpoints-bucket")
            
            expected_path = f"sandbox_checkpoints/{checkpoint_id}"
            self.assertEqual(checkpoint_info["path"], expected_path)

    async def test_delete_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_instance = MagicMock()
            gcs_config = GCSConfig(
                metadata_mount_path=temp_dir,
                metadata_bucket="my-metadata-bucket",
                sandbox_checkpoint_mount_path=temp_dir,
                sandbox_checkpoint_bucket="my-checkpoints-bucket",
            )

            handle = await SandboxHandle.create_persistent(
                sandbox_id="persistent-sandbox",
                instance=mock_instance,
                idle_timeout=300,
                gcs_config=gcs_config,
                lock_factory=MagicMock(),
            )

            metadata_dir = os.path.dirname(handle.metadata_path)
            self.assertTrue(os.path.exists(metadata_dir))

            await handle.delete_metadata()

            self.assertFalse(os.path.exists(metadata_dir))


class TestSandboxHandleProperties(unittest.TestCase):
    def test_is_sandbox_checkpointable(self):
        eph_handle = SandboxHandle.create_ephemeral("eph", MagicMock(), 120)
        self.assertFalse(eph_handle.is_sandbox_checkpointable)

        meta_false = GCSSandboxMetadata("id", time.time(), enable_sandbox_checkpoint=False)
        pers_handle_false = SandboxHandle.create_ephemeral("pers", MagicMock(), 120)
        pers_handle_false.gcs_metadata = meta_false
        self.assertFalse(pers_handle_false.is_sandbox_checkpointable)

        meta_true = GCSSandboxMetadata("id", time.time(), enable_sandbox_checkpoint=True)
        pers_handle_true = SandboxHandle.create_ephemeral("pers", MagicMock(), 120)
        pers_handle_true.gcs_metadata = meta_true
        self.assertTrue(pers_handle_true.is_sandbox_checkpointable)


class TestSandboxHandlePathBuilders(unittest.TestCase):
    def test_build_lock_blob_name(self):
        self.assertEqual(
            SandboxHandle.build_lock_blob_name("my-sandbox"),
            "sandboxes/my-sandbox/lock.json"
        )

    def test_build_metadata_path(self):
        gcs_config = GCSConfig(metadata_mount_path="/mnt/gcs/meta")
        self.assertEqual(
            SandboxHandle.build_metadata_path(gcs_config, "my-sandbox"),
            "/mnt/gcs/meta/sandboxes/my-sandbox/metadata.json"
        )

    def test_metadata_path_property(self):
        gcs_config = GCSConfig(metadata_mount_path="/mnt/gcs/meta")
        handle = SandboxHandle.create_ephemeral("my-sandbox", MagicMock(), 120, gcs_config=gcs_config)
        self.assertEqual(handle.metadata_path, "/mnt/gcs/meta/sandboxes/my-sandbox/metadata.json")



class TestAcquireLockHelper(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_lock_callback_binding(self):
        """
        Tests that _acquire_lock correctly binds the sandbox_id to the callbacks.
        """
        mock_lock_factory = MagicMock(spec=LockFactory)
        mock_lock = AsyncMock()
        mock_lock_factory.create_lock.return_value = mock_lock

        on_release_requested = AsyncMock()
        on_renewal_error = AsyncMock()

        sandbox_id = "test-sandbox"
        await _acquire_lock(
            lock_factory=mock_lock_factory,
            sandbox_id=sandbox_id,
            on_release_requested=on_release_requested,
            on_renewal_error=on_renewal_error,
        )

        # Get the partial functions that were passed to create_lock
        _, kwargs = mock_lock_factory.create_lock.call_args
        release_callback = kwargs["on_release_requested"]
        renewal_callback = kwargs["on_renewal_error"]

        # Ensure they are partial functions
        self.assertIsInstance(release_callback, partial)
        self.assertIsInstance(renewal_callback, partial)

        # Call the partials and assert that the original functions were called with the sandbox_id
        await release_callback()
        on_release_requested.assert_awaited_once_with(sandbox_id)

        await renewal_callback()
        on_renewal_error.assert_awaited_once_with(sandbox_id)

