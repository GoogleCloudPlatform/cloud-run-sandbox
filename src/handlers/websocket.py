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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException
from src.sandbox.manager import SandboxManager
from src.sandbox.interface import SandboxCreationError, SandboxExecutionError, SandboxExecutionInProgressError, SandboxStreamClosed, SandboxOperationError, SandboxCheckpointError, SandboxRestoreError, SandboxSnapshotFilesystemError, SandboxError, StatusNotifier, UnsupportedLanguageError, SandboxPermissionError
from src.sandbox.types import SandboxStateEvent, CodeLanguage
import asyncio
import logging
from functools import partial
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

# This will be replaced by the configured manager instance at startup
manager: Optional[SandboxManager] = None


class WebSocketStatusNotifier(StatusNotifier):
    """
    An implementation of the StatusNotifier that sends status updates over a WebSocket.
    """
    def __init__(self, websocket: WebSocket):
        self._websocket = websocket

    async def send_status(self, status: SandboxStateEvent):
        """Sends a status event to the client."""
        try:
            await self._websocket.send_json({"event": "status_update", "status": status.value})
        except Exception as e:
            logger.warning(f"Failed to send status update to client: {e}")

class WebsocketHandler:
    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.sandbox = None
        self.active_tasks = set()
        self.status_notifier = WebSocketStatusNotifier(websocket)

    async def _websocket_lifecycle(self, setup_coro):
        """
        Manages the entire lifecycle of a websocket connection, ensuring
        proper setup and teardown.
        """
        await self.websocket.accept()
        try:
            # Run the specific setup logic (create or attach)
            success = await setup_coro()
            if success:
                # If setup was successful, start the main execution loop
                await self.execution_loop()
        except (WebSocketDisconnect, WebSocketException) as e:
            # This will catch disconnects that happen outside the main execution loop
            # (e.g., during sandbox creation).
            logger.info(f"WebSocket connection closed: {e}")
        finally:
            # Ensure all background tasks are cancelled on handler exit.
            for task in self.active_tasks:
                task.cancel()
            if self.sandbox:
                self.sandbox.is_attached = False
                logger.info(f"Finished handling websocket for sandbox {self.sandbox.sandbox_id}")

    async def _setup_create(self):
        """Sets up the handler for a new sandbox."""
        try:
            # 1. Wait for the initial configuration message
            init_message = await self.websocket.receive_json()
            idle_timeout = init_message.get("idle_timeout", 300)
            enable_checkpoint = init_message.get("enable_checkpoint", False)
            enable_idle_timeout_auto_checkpoint = init_message.get("enable_idle_timeout_auto_checkpoint", False)
            enable_sandbox_handoff = init_message.get("enable_sandbox_handoff", False)
            filesystem_snapshot_name = init_message.get("filesystem_snapshot_name")

            await self.send_status(SandboxStateEvent.SANDBOX_CREATING)

            if enable_checkpoint and not manager.is_sandbox_checkpointing_enabled:
                raise SandboxCreationError("Checkpointing is not enabled on the server.")

            if filesystem_snapshot_name and not manager.is_filesystem_snapshotting_enabled:
                raise SandboxCreationError("Filesystem snapshot is not enabled on the server.")

            self.sandbox = await manager.create_sandbox(
                idle_timeout=idle_timeout,
                enable_checkpoint=enable_checkpoint,
                enable_idle_timeout_auto_checkpoint=enable_idle_timeout_auto_checkpoint,
                enable_sandbox_handoff=enable_sandbox_handoff,
                filesystem_snapshot_name=filesystem_snapshot_name,
                status_notifier=self.status_notifier
            )
            self.sandbox.is_attached = True
            sandbox_token = await self.sandbox.get_sandbox_token()
            await self.websocket.send_json({
                "event": "sandbox_id",
                "sandbox_id": self.sandbox.sandbox_id,
                "sandbox_token": sandbox_token,
            })
            
            # 3. Signal that the sandbox is ready
            await self.send_status(SandboxStateEvent.SANDBOX_RUNNING)
            return True  # Indicates success
        except SandboxCreationError as e:
            logger.error(f"Sandbox creation failed: {e}")
            await self.handle_error(e, close_connection=True)
            return False  # Indicates failure

    async def _setup_attach(self, sandbox_id: str, sandbox_token: str):
        """Sets up the handler for an existing sandbox."""
        try:
            if not sandbox_token:
                raise SandboxPermissionError("Sandbox token is missing.")

            sandbox = manager.get_sandbox(sandbox_id)
            if not sandbox:
                if manager.is_sandbox_checkpointing_enabled:
                    await self.send_status(SandboxStateEvent.SANDBOX_RESTORING)
                    sandbox = await manager.restore_sandbox(sandbox_id, status_notifier=self.status_notifier)
                
                if not sandbox:
                    await self.send_status(SandboxStateEvent.SANDBOX_NOT_FOUND)
                    await self.websocket.close(code=1011)
                    return False  # Indicates failure
            else:
                manager.update_status_notifier(sandbox_id, self.status_notifier)
            
            # Verify the token
            expected_token = await sandbox.get_sandbox_token()
            if expected_token != sandbox_token:
                raise SandboxPermissionError("Invalid sandbox token.")

            if sandbox.is_attached:
                await self.send_status(SandboxStateEvent.SANDBOX_IN_USE)
                await self.websocket.close(code=1011)
                return False

            self.sandbox = sandbox
            self.sandbox.is_attached = True
            await self.send_status(SandboxStateEvent.SANDBOX_RUNNING)
            return True  # Indicates success
        except SandboxPermissionError as e:
            await self.handle_error(e, close_connection=True)
            return False
        except Exception as e:
            e = SandboxRestoreError(f"Failed to restore sandbox {sandbox_id}: {e}")
            await self.handle_error(e, close_connection=True)
            return False

    async def handle_create(self):
        """Public entrypoint to handle a 'create' websocket connection."""
        await self._websocket_lifecycle(self._setup_create)

    async def handle_attach(self, sandbox_id: str, sandbox_token: str):
        """Public entrypoint to handle an 'attach' websocket connection."""
        setup_coro = partial(self._setup_attach, sandbox_id=sandbox_id, sandbox_token=sandbox_token)
        await self._websocket_lifecycle(setup_coro)

    async def execution_loop(self):
        """
        Continuously waits for code execution requests and launches them in
        the background.
        """
        try:
            while True:
                message = await self.websocket.receive_json()
                task = None
                if message.get("action") == "checkpoint":
                    task = asyncio.create_task(self.handle_checkpoint())
                elif message.get("action") == "snapshot_filesystem":
                    task = asyncio.create_task(self.handle_snapshot_filesystem(message))
                elif message.get("action") == "kill_process":
                    task = asyncio.create_task(self.handle_kill_process())
                elif message.get("action") == "kill_sandbox":
                    task = asyncio.create_task(self.handle_kill_sandbox())
                elif message.get("event") == "stdin":
                    task = asyncio.create_task(self.handle_stdin(message))
                elif message.get("action") == "reconnect":
                    task = asyncio.create_task(self.reconnect_and_stream(message))
                else:
                    task = asyncio.create_task(self.run_and_stream(message))
                
                if task:
                    self.active_tasks.add(task)
                    task.add_done_callback(self.active_tasks.discard)
        except (WebSocketDisconnect, WebSocketException):
            logger.info(f"Client disconnected from sandbox {self.sandbox.sandbox_id if self.sandbox else 'unknown'}")

    async def handle_kill_process(self):
        """Handles a kill_process request from the client."""
        try:
            if not self.sandbox.is_execution_running:
                await self.send_status(SandboxStateEvent.SANDBOX_EXECUTION_FORCE_KILLED)
                return
            await self.sandbox.kill_exec_process()
            await self.send_status(SandboxStateEvent.SANDBOX_EXECUTION_FORCE_KILLED)
        except Exception as e:
            logger.error(f"Kill failed for sandbox {self.sandbox.sandbox_id}", exc_info=e)
            await self.send_status(SandboxStateEvent.SANDBOX_EXECUTION_FORCE_KILL_ERROR)

    async def handle_kill_sandbox(self):
        """Handles a kill_sandbox request from the client."""
        try:
            await manager.kill_sandbox(self.sandbox.sandbox_id)
            await self.websocket.close(code=1000)
        except Exception as e:
            logger.error(f"Kill sandbox failed for sandbox {self.sandbox.sandbox_id}", exc_info=e)
            await self.handle_error(e, close_connection=True)

    async def handle_checkpoint(self):
        """Handles a checkpoint request from the client."""
        try:
            await self.send_status(SandboxStateEvent.SANDBOX_CHECKPOINTING)
            await manager.checkpoint_sandbox(self.sandbox.sandbox_id)
            await self.send_status(SandboxStateEvent.SANDBOX_CHECKPOINTED)
            await self.websocket.close(code=1000)
        except SandboxExecutionInProgressError as e:
            await self.handle_error(e, close_connection=False)
        except Exception as e:
            logger.error(f"Checkpoint failed for sandbox {self.sandbox.sandbox_id}", exc_info=e)
            e = SandboxCheckpointError(f"Failed to checkpoint sandbox {self.sandbox.sandbox_id}: {e}")
            await self.handle_error(e, close_connection=True)

    async def handle_snapshot_filesystem(self, message: dict):
        """Handles a snapshot filesystem request from the client."""
        try:
            await self.send_status(SandboxStateEvent.SANDBOX_FILESYSTEM_SNAPSHOT_CREATING)
            if not manager.is_filesystem_snapshotting_enabled:
                logger.error(f"Filesystem snapshot rejected for sandbox {self.sandbox.sandbox_id}: feature is not enabled on the server.")
                raise SandboxOperationError("Filesystem snapshot is not enabled on the server.")

            snapshot_name = message['name']
            await manager.snapshot_filesystem(self.sandbox.sandbox_id, snapshot_name)
            await self.send_status(SandboxStateEvent.SANDBOX_FILESYSTEM_SNAPSHOT_CREATED)
        except (SandboxOperationError, SandboxSnapshotFilesystemError) as e:
            e = SandboxSnapshotFilesystemError(f"Failed to snapshot filesystem for sandbox {self.sandbox.sandbox_id}: {e}")
            await self.handle_error(e, close_connection=False)
        except Exception as e:
            e = SandboxError(f"Failed to snapshot filesystem for sandbox {self.sandbox.sandbox_id}: {e}")
            await self.handle_error(e, close_connection=True)

    async def handle_stdin(self, message: dict):
        """Handles a stdin message from the client."""
        try:
            data = message['data']
            await self.sandbox.write_stdin(data)
        except (KeyError, ValueError) as e:
            await self.handle_error(e, close_connection=False, message=message)
        except Exception as e:
            logger.error(f"Unexpected error during stdin write: {e}")
            await self.handle_error(e, close_connection=False, message=message)

    async def run_and_stream(self, message: dict):
        """
        Handles a single code execution request, including streaming the output.
        """
        is_execution_done_sent = False
        try:
            language_str = message.get('language')
            if not language_str or language_str not in [lang.value for lang in CodeLanguage]:
                supported_languages = ", ".join([lang.value for lang in CodeLanguage])
                raise UnsupportedLanguageError(
                    f"Unsupported language: '{language_str}'. Supported languages are: {supported_languages}"
                )
            language = CodeLanguage(language_str)
            code = message['code']

            await self.sandbox.execute(language, code=code)
            
            async for event in self.sandbox.stream_outputs():
                if event["type"] == "status_update":
                    status = SandboxStateEvent(event["status"])
                    if status == SandboxStateEvent.SANDBOX_EXECUTION_DONE:
                        is_execution_done_sent = True
                    await self.send_status(status)
                else:
                    await self.websocket.send_json({
                        "event": event["type"].value,
                        "data": event["data"]
                    })

        except SandboxStreamClosed:
            # This is the expected end of a stream, not an error.
            pass
        except (SandboxOperationError, KeyError, ValueError) as e:
            await self.handle_error(e, close_connection=False, message=message)
        except Exception as e:
            logger.error(f"Unexpected error during execution: {e}")
            await self.handle_error(e, close_connection=True, message=message)
        finally:
            if not self.sandbox.is_execution_running and not is_execution_done_sent:
                await self.send_status(SandboxStateEvent.SANDBOX_EXECUTION_DONE)

    async def reconnect_and_stream(self, message: dict):
        """
        Handles a reconnect request, including streaming the output.
        """
        is_execution_done_sent = False
        try:
            async for event in self.sandbox.stream_outputs():
                if event["type"] == "status_update":
                    status = SandboxStateEvent(event["status"])
                    if status == SandboxStateEvent.SANDBOX_EXECUTION_DONE:
                        is_execution_done_sent = True
                    await self.send_status(status)
                else:
                    await self.websocket.send_json({
                        "event": event["type"].value,
                        "data": event["data"]
                    })
            
            # After the stream is exhausted, if the execution is no longer running
            # and the client hasn't been sent the final status, send it now.
            if not self.sandbox.is_execution_running and not is_execution_done_sent:
                await self.send_status(SandboxStateEvent.SANDBOX_EXECUTION_DONE)

        except SandboxStreamClosed:
            # This is the expected end of a stream, not an error.
            if not self.sandbox.is_execution_running and not is_execution_done_sent:
                await self.send_status(SandboxStateEvent.SANDBOX_EXECUTION_DONE)
        except (SandboxOperationError, KeyError, ValueError) as e:
            await self.handle_error(e, close_connection=False, message=message)
        except Exception as e:
            logger.error(f"Unexpected error during execution: {e}")
            await self.handle_error(e, close_connection=True, message=message)

    async def send_status(self, status: SandboxStateEvent):
        await self.websocket.send_json({"event": "status_update", "status": status.value})

    async def handle_error(self, e: Exception, close_connection: bool, message: dict = None):
        error_status = SandboxStateEvent.SANDBOX_ERROR

        if isinstance(e, SandboxPermissionError):
            error_status = SandboxStateEvent.SANDBOX_PERMISSION_DENIAL_ERROR
        elif isinstance(e, UnsupportedLanguageError):
            error_status = SandboxStateEvent.SANDBOX_EXECUTION_UNSUPPORTED_LANGUAGE_ERROR
        elif isinstance(e, SandboxRestoreError):
            error_status = SandboxStateEvent.SANDBOX_RESTORE_ERROR
        elif isinstance(e, SandboxExecutionInProgressError):
            error_status = SandboxStateEvent.SANDBOX_EXECUTION_IN_PROGRESS_ERROR
        elif isinstance(e, SandboxCheckpointError):
            error_status = SandboxStateEvent.SANDBOX_CHECKPOINT_ERROR
        elif isinstance(e, SandboxSnapshotFilesystemError):
            error_status = SandboxStateEvent.SANDBOX_FILESYSTEM_SNAPSHOT_ERROR
        elif isinstance(e, SandboxOperationError):
            error_status = SandboxStateEvent.SANDBOX_EXECUTION_ERROR
        elif isinstance(e, (KeyError, ValueError)):
            if message and message.get("event") == "stdin":
                e = SandboxExecutionError("Invalid stdin message format. 'data' field is required.")
                error_status = SandboxStateEvent.SANDBOX_STDIN_ERROR
            else:
                supported_languages = ", ".join([lang.value for lang in CodeLanguage])
                e = SandboxExecutionError(
                    "Invalid message format. 'language' and 'code' fields are required. "
                    f"Supported languages are: {supported_languages}"
                )
                error_status = SandboxStateEvent.SANDBOX_EXECUTION_ERROR
        elif isinstance(e, SandboxCreationError):
            error_status = SandboxStateEvent.SANDBOX_CREATION_ERROR
        else:
            error_status = SandboxStateEvent.SANDBOX_ERROR

        await self.send_status(error_status)
        await self.websocket.send_json({"event": "error", "message": str(e)})

        if close_connection:
            await self.websocket.close(code=4000)

@router.websocket("/create")
async def create(websocket: WebSocket):
    handler = WebsocketHandler(websocket)
    await handler.handle_create()

@router.websocket("/attach/{sandbox_id}")
async def attach(websocket: WebSocket, sandbox_id: str, sandbox_token: str):
    """
    Attaches to an existing sandbox.
    """
    handler = WebsocketHandler(websocket)
    await handler.handle_attach(sandbox_id, sandbox_token)
