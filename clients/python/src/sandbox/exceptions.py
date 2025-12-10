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

class SandboxException(Exception):
    """Base exception for the sandbox client."""
    pass

class SandboxConnectionError(SandboxException):
    """Raised when there is an issue with the WebSocket connection."""
    pass

class SandboxCreationError(SandboxException):
    """Raised when the server fails to create a sandbox."""
    pass

class SandboxExecutionError(SandboxException):
    """Raised when the server reports an error during code execution."""
    pass

class SandboxStateError(SandboxException):
    """Raised when an operation is attempted on a sandbox in an invalid state."""
    pass

class SandboxFilesystemSnapshotError(SandboxException):
    """Raised when a filesystem snapshot fails."""
    pass

class SandboxCheckpointError(SandboxException):
    """Raised when a checkpoint fails."""
    pass
