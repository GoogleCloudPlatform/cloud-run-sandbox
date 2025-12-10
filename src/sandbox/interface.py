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

from abc import ABC, abstractmethod
from enum import Enum
from .types import SandboxOutputEvent, CodeLanguage, SandboxStateEvent

class SandboxState(str, Enum):
    """Represents the lifecycle state of a sandbox."""
    INITIALIZED = "INITIALIZED"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"

class SandboxError(Exception):
    """Base exception for sandbox-related errors."""
    pass

class SandboxCreationError(SandboxError):
    """Raised when a sandbox fails to create."""
    pass

class SandboxOperationError(SandboxError):
    """Raised when an operation on a sandbox fails."""
    pass

class SandboxPermissionError(SandboxError):
    """Raised when a permission error occurs."""
    pass

class SandboxCheckpointError(SandboxOperationError):
    """Raised when a sandbox checkpoint operation fails."""
    pass

class SandboxExecutionInProgressError(SandboxCheckpointError):
    """Raised when a checkpoint is attempted during an active execution."""
    pass

class SandboxRestoreError(SandboxOperationError):
    """Raised when a sandbox restore operation fails."""
    pass

class SandboxNotFoundError(SandboxRestoreError):
    """Raised when a sandbox to be restored is not found."""
    pass

class SandboxSnapshotFilesystemError(SandboxOperationError):
    """Raised when a sandbox filesystem snapshot operation fails."""
    pass

class SandboxExecutionError(SandboxOperationError):
    """Raised when code execution within a sandbox fails."""
    pass

class UnsupportedLanguageError(SandboxExecutionError):
    """Raised when an unsupported language is specified for execution."""
    pass

class SandboxStreamClosed(SandboxError):
    """Raised by the stream_outputs() generator when the output stream is closed."""
    pass

class StatusNotifier(ABC):
    """
    An interface for sending status updates from the sandbox back to the client.
    """
    @abstractmethod
    async def send_status(self, status: SandboxStateEvent):
        """Sends a status event to the client."""
        pass

class SandboxInterface(ABC):
    """Defines the interface for all sandbox implementations."""
    
    @property
    @abstractmethod
    def sandbox_id(self):
        pass

    @property
    @abstractmethod
    def is_execution_running(self) -> bool:
        pass

    @property
    def is_attached(self) -> bool:
        return False

    @is_attached.setter
    def is_attached(self, value: bool):
        pass

    @abstractmethod
    async def create(self):
        """
        Performs initial setup for the sandbox.
        Raises SandboxCreationError on failure.
        """
        pass

    @abstractmethod
    async def execute(self, language: CodeLanguage, code: str):
        """
        Executes code in the sandbox.
        Raises SandboxOperationError on failure.
        """
        pass

    @abstractmethod
    async def kill_exec_process(self):
        """
        Forcefully kills the running exec process.
        """
        pass

    @abstractmethod
    async def delete(self):
        pass

    @abstractmethod
    async def stream_outputs(self):
        """
        Yields a stream of SandboxOutputEvent objects one at a time.
        
        A SandboxOutputEvent is a dictionary-like object (a TypedDict) with a
        fixed structure: {'type': OutputType, 'data': str}.

        Raises SandboxStreamClosed when the stream is finished.
        """
        pass

    @abstractmethod
    async def write_stdin(self, data: str):
        """
        Writes data to stdin of the running process.
        """
        pass

    @abstractmethod
    async def checkpoint(self, checkpoint_path: str, force: bool = False) -> None:
        """
        Creates a checkpoint of the sandbox's state.
        If force is True, the checkpoint will be created even if an execution
        is in progress.
        """
        pass

    @abstractmethod
    async def restore(self, checkpoint_path: str) -> None:
        """
        Restores the sandbox's state from a checkpoint.
        """
        pass

    @abstractmethod
    async def snapshot_filesystem(self, snapshot_path: str) -> None:
        """
        Creates a snapshot of the sandbox's filesystem.
        """
        pass

    @abstractmethod
    async def set_sandbox_token(self, token: str) -> None:
        """
        Sets the sandbox token.

        Args:
            token: The sandbox token.
        """
        pass

    @abstractmethod
    async def get_sandbox_token(self) -> str:
        """
        Gets the sandbox token.
        """
        pass