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

from enum import Enum
from typing import TypedDict

class CodeLanguage(Enum):
    """Enumeration for the supported code languages."""
    PYTHON = "python"
    BASH = "bash"

class OutputType(Enum):
    """Enumeration for the type of sandbox output stream."""
    STDOUT = "stdout"
    STDERR = "stderr"

class SandboxOutputEvent(TypedDict):
    """
    A TypedDict representing a single output event from a sandbox.
    'type' indicates the stream (stdout or stderr), and 'data' is the content.
    """
    type: OutputType
    data: str

class SandboxStateEvent(Enum):
    """Enumeration for the lifecycle state events of a sandbox."""
    # A general error occurred within the sandbox.
    SANDBOX_ERROR = "SANDBOX_ERROR"
    # The sandbox is being created and initialized.
    SANDBOX_CREATING = "SANDBOX_CREATING"
    # The sandbox is running and ready to process code.
    SANDBOX_RUNNING = "SANDBOX_RUNNING"
    # The requested sandbox could not be found on any active instance.
    SANDBOX_NOT_FOUND = "SANDBOX_NOT_FOUND"
    # A non-recoverable error occurred during the initial creation of the sandbox.
    SANDBOX_CREATION_ERROR = "SANDBOX_CREATION_ERROR"
    # An error occurred during the execution of code in the sandbox.
    SANDBOX_EXECUTION_ERROR = "SANDBOX_EXECUTION_ERROR"
    # The specified language is not supported.
    SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR = "SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR"
    # The sandbox is executing code.
    SANDBOX_EXECUTION_RUNNING = "SANDBOX_EXECUTION_RUNNING"
    # The sandbox has finished executing code.
    SANDBOX_EXECUTION_DONE = "SANDBOX_EXECUTION_DONE"
    # The sandbox is already in use by another client.
    SANDBOX_IN_USE = "SANDBOX_IN_USE"
    # An error occurred while writing to stdin.
    SANDBOX_STDIN_ERROR = "SANDBOX_STDIN_ERROR"
    # A running process was forcefully killed.
    SANDBOX_EXECUTION_FORCE_KILLED = "SANDBOX_EXECUTION_FORCE_KILLED"
    # An error occurred during the forced kill of a running process.
    SANDBOX_EXECUTION_FORCE_KILL_ERROR = "SANDBOX_EXECUTION_FORCE_KILL_ERROR"
    # The sandbox is being checkpointed.
    SANDBOX_CHECKPOINTING = "SANDBOX_CHECKPOINTING"
    # The sandbox has been successfully checkpointed.
    SANDBOX_CHECKPOINTED = "SANDBOX_CHECKPOINTED"
    # An error occurred during the checkpointing of the sandbox.
    SANDBOX_CHECKPOINT_ERROR = "SANDBOX_CHECKPOINT_ERROR"
    # The sandbox is being restored from a checkpoint.
    SANDBOX_RESTORING = "SANDBOX_RESTORING"
    # An error occurred during the restoration of the sandbox.
    SANDBOX_RESTORE_ERROR = "SANDBOX_RESTORE_ERROR"
    # A checkpoint was attempted while an execution was in progress.
    SANDBOX_EXECUTION_IN_PROGRESS_ERROR = "SANDBOX_EXECUTION_IN_PROGRESS_ERROR"
    # The sandbox filesystem is being snapshotted.
    SANDBOX_FILESYSTEM_SNAPSHOT_CREATING = "SANDBOX_FILESYSTEM_SNAPSHOT_CREATING"
    # The sandbox filesystem has been successfully snapshotted.
    SANDBOX_FILESYSTEM_SNAPSHOT_CREATED = "SANDBOX_FILESYSTEM_SNAPSHOT_CREATED"
    # An error occurred during the snapshotting of the sandbox filesystem.
    SANDBOX_FILESYSTEM_SNAPSHOT_ERROR = "SANDBOX_FILESYSTEM_SNAPSHOT_ERROR"
    # Sandbox deletion in progress.
    SANDBOX_DELETING = "SANDBOX_DELETING"
    # Sandbox has been deleted.
    SANDBOX_DELETED = "SANDBOX_DELETED"
    # The followings are lock acquision, renew and release events.
    SANDBOX_LOCK_ACUIRING = "SANDBOX_LOCK_ACQUIRING"
    SANDBOX_LOCK_ACQUIRED = "SANDBOX_LOCK_ACQUIRED"
    SANDBOX_LOCK_RELEASE_REQUESTED = "SANDBOX_LOCK_RELEASE_REQUESTED"
    SANDBOX_LOCK_RENEWAL_ERROR = "SANDBOX_LOCK_RENEWAL_ERROR"
    SANDBOX_LOCK_RELEASING = "SANDBOX_LOCK_RELEASING"
    SANDBOX_LOCK_RELEASED = "SANDBOX_LOCK_RELEASED"
    SANDBOX_KILLING = "SANDBOX_KILLING"
    SANDBOX_KILLED = "SANDBOX_KILLED"
    SANDBOX_KILL_ERROR = "SANDBOX_KILL_ERROR"
    SANDBOX_PERMISSION_DENIAL_ERROR = "SANDBOX_PERMISSION_DENIAL_ERROR"



