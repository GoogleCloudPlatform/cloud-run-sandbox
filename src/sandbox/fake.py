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
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Type
from .interface import SandboxInterface, SandboxCreationError, SandboxExecutionInProgressError, SandboxOperationError, SandboxSnapshotFilesystemError, SandboxStreamClosed, SandboxState
from .types import SandboxOutputEvent, CodeLanguage, SandboxStateEvent

logger = logging.getLogger(__name__)

@dataclass
class ExecConfig:
    """Configuration for a single execution within the FakeSandbox."""
    output_stream: List[SandboxOutputEvent] = field(default_factory=list)
    expected_language: Optional[CodeLanguage] = None
    expected_code: Optional[str] = None
    expected_stdin: List[str] = field(default_factory=list)
    exec_error: Optional[Type[Exception]] = None
    connect_error: Optional[Type[Exception]] = None

@dataclass
class FakeSandboxConfig:
    """Configuration for the FakeSandbox."""
    create_should_fail: bool = False
    checkpoint_should_fail: bool = False
    restore_should_fail: bool = False
    snapshot_filesystem_should_fail: bool = False
    executions: List[ExecConfig] = field(default_factory=list)

class FakeSandbox(SandboxInterface):
    """
    A configurable, fake, in-memory sandbox for testing that supports
    scripted responses for multiple, sequential executions and a strict
    state machine.
    """
    def __init__(self, sandbox_id, config: FakeSandboxConfig = None):
        self._sandbox_id = sandbox_id
        self._config = config or FakeSandboxConfig()
        self._state = SandboxState.INITIALIZED
        self._exec_count = 0
        self._is_attached = False
        self._shutting_down = False
        self._sandbox_token = None

    @property
    def sandbox_id(self):
        return self._sandbox_id

    @property
    def is_attached(self) -> bool:
        return self._is_attached

    @is_attached.setter
    def is_attached(self, value: bool):
        self._is_attached = value

    async def create(self):
        if self._state != SandboxState.INITIALIZED:
            raise SandboxOperationError(f"Cannot create a sandbox that is not in the INITIALIZED state (current state: {self._state})")
        if self._config.create_should_fail:
            self._state = SandboxState.FAILED
            raise SandboxCreationError("Fake sandbox failed to create as configured.")
        
        logger.info(f"Fake sandbox {self.sandbox_id}: CREATED.")
        self._state = SandboxState.RUNNING
        await asyncio.sleep(0.01)

    async def execute(self, language: CodeLanguage, code: str):
        if self._shutting_down:
            raise SandboxOperationError("Sandbox is shutting down and cannot start new executions.")
        if self._state != SandboxState.RUNNING:
            raise SandboxOperationError(f"Cannot execute code in a sandbox that is not in the RUNNING state (current state: {self._state})")

        current_exec_config = self._get_current_exec()
        if not current_exec_config:
            raise AssertionError("Unexpected call to execute().")

        # Verify expectations
        if current_exec_config.expected_language is not None:
            assert language == current_exec_config.expected_language
        if current_exec_config.expected_code is not None:
            assert code == current_exec_config.expected_code
        
        # Raise a configured error, if any
        if current_exec_config.exec_error:
            raise current_exec_config.exec_error("Fake sandbox failed to execute as configured.")

        logger.info(f"Fake sandbox {self.sandbox_id}: EXECUTING ({self._exec_count + 1}).")

    @property
    def is_execution_running(self) -> bool:
        # In the fake sandbox, an execution is "running" if it has been started
        # but the stream_outputs() stream has not yet been fully consumed.
        return self._get_current_exec() is not None

    async def stream_outputs(self):
        """
        Yields the configured output messages for the current execution.
        """
        if not self.is_execution_running:
            raise SandboxStreamClosed()

        current_exec_config = self._get_current_exec()
        if current_exec_config.connect_error:
            raise current_exec_config.connect_error("Fake sandbox failed to connect as configured.")

        yield {"type": "status_update", "status": SandboxStateEvent.SANDBOX_EXECUTION_RUNNING.value}

        for message in current_exec_config.output_stream:
            yield message
            await asyncio.sleep(0.01)
        
        yield {"type": "status_update", "status": SandboxStateEvent.SANDBOX_EXECUTION_DONE.value}
        
        self._exec_count += 1
        raise SandboxStreamClosed()

    def _get_current_exec(self) -> Optional[ExecConfig]:
        if self._exec_count < len(self._config.executions):
            return self._config.executions[self._exec_count]
        return None

    async def delete(self):
        logger.info(f"Fake sandbox {self.sandbox_id}: DELETING.")
        self._state = SandboxState.STOPPED
        logger.info(f"Fake sandbox {self.sandbox_id}: DELETED.")

    async def write_stdin(self, data: str):
        """Writes data to the stdin of the process."""
        logger.info(f"Fake sandbox {self.sandbox_id}: writing to stdin: {data}")
        current_exec_config = self._get_current_exec()
        if not current_exec_config:
            raise AssertionError("Unexpected call to write_stdin().")

        if not current_exec_config.expected_stdin:
            raise AssertionError(f"Received unexpected stdin: {data}")

        expected_data = current_exec_config.expected_stdin.pop(0)
        assert data == expected_data

    async def checkpoint(self, checkpoint_path: str, force: bool = False) -> None:
        """
        Simulates checkpointing by creating a dummy file.
        """
        if self._state != SandboxState.RUNNING:
            raise SandboxOperationError(f"Cannot checkpoint a sandbox that is not in the RUNNING state (current state: {self._state})")
        if self.is_execution_running and not force:
            raise SandboxExecutionInProgressError("Cannot checkpoint while an execution is in progress.")
        if self._config.checkpoint_should_fail:
            raise SandboxOperationError("Fake sandbox failed to checkpoint as configured.")
        
        if force:
            self._shutting_down = True

        logger.info(f"Fake sandbox {self.sandbox_id}: CHECKPOINTING to {checkpoint_path}.")
        # The provided path is a directory. Create a dummy file inside it.
        os.makedirs(checkpoint_path, exist_ok=True)
        with open(os.path.join(checkpoint_path, "checkpoint.img"), "w") as f:
            f.write("checkpoint_data")
        
        self._state = SandboxState.CHECKPOINTED
        logger.info(f"Fake sandbox {self.sandbox_id}: CHECKPOINTED.")

    async def restore(self, checkpoint_path: str) -> None:
        """
        Simulates restoring by checking for the dummy checkpoint file.
        """
        if self._state != SandboxState.INITIALIZED:
            raise SandboxOperationError(f"Cannot restore a sandbox that is not in the INITIALIZED state (current state: {self._state})")
        if self._config.restore_should_fail:
            raise SandboxOperationError("Fake sandbox failed to restore as configured.")

        logger.info(f"Fake sandbox {self.sandbox_id}: RESTORING from {checkpoint_path}.")
        if not os.path.exists(checkpoint_path):
            raise SandboxOperationError(f"Checkpoint file not found at {checkpoint_path}")
        
        self._state = SandboxState.RUNNING
        logger.info(f"Fake sandbox {self.sandbox_id}: RESTORED.")

    async def snapshot_filesystem(self, snapshot_path: str) -> None:
        if self._config.snapshot_filesystem_should_fail:
            raise SandboxSnapshotFilesystemError("Fake sandbox failed to snapshot filesystem as configured.")
        logger.info(f"Fake sandbox {self.sandbox_id}: SNAPSHOTTING to {snapshot_path}.")
        with open(snapshot_path, "w") as f:
            f.write("snapshot_data")

    async def kill_exec_process(self):
        """Simulates killing the execution process."""
        logger.info(f"Fake sandbox {self.sandbox_id}: KILLING EXEC PROCESS.")
        self._shutting_down = True

    async def set_sandbox_token(self, token: str) -> None:
        self._sandbox_token = token

    async def get_sandbox_token(self) -> str:
        """Gets the sandbox token."""
        return self._sandbox_token
