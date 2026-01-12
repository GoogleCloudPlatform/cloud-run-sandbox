
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
import json
import logging
import weakref
import websockets
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2 import id_token
import google.auth
import google.auth.exceptions

from .types import MessageKey, EventType, SandboxEvent
from .exceptions import SandboxConnectionError, SandboxCreationError, SandboxStateError, SandboxFilesystemSnapshotError, SandboxCheckpointError
from .process import SandboxProcess
from .connection import Connection

class Sandbox:
    """
    Represents a connection to a Cloud Run Sandbox, used to execute commands.
    """
    _HINT_AUTH_PERMISSION = " (Hint: Permission denied or missing authentication. Ensure you have enabled 'use_google_auth=True' in the Sandbox.create/attach options, and that your account has the 'Cloud Run Invoker' (roles/run.invoker) role on this service.)"
    _TROUBLESHOOTING_URL = "https://docs.cloud.google.com/run/docs/troubleshooting"

    def __init__(self, initial_state: str, enable_debug=False, debug_label=""):
        self._connection = None
        self._sandbox_id = None
        self._sandbox_token = None
        self._active_process = None
        self._ready_event = asyncio.Event()
        self._killed_event = asyncio.Event()
        self._filesystem_snapshot_event = asyncio.Event()
        self._filesystem_snapshot_error = None
        self._checkpoint_event = asyncio.Event()
        self._checkpoint_error = None
        self._error_on_ready = None
        self._state = initial_state
        self._shutdown_lock = asyncio.Lock()
        self._enable_debug = enable_debug
        self._debug_label = debug_label
        self._is_kill_intentionally = False
        self._enable_auto_reconnect = False
        self._should_reconnect_internal = False
        self._base_url = None
        self._use_google_auth = False

    @property
    def sandbox_id(self):
        """The unique ID of the sandbox."""
        return self._sandbox_id

    @property
    def sandbox_token(self):
        """The token for the sandbox."""
        return self._sandbox_token

    @staticmethod
    def _get_id_token(url: str):
        logger = logging.getLogger(__name__)
        parsed_url = urlparse(url)
        audience = f"https://{parsed_url.netloc}"
        logger.debug(f"Derived OIDC audience {audience} from connection URL {url}")
        auth_req = Request()

        # 1. Try to use google.auth.default() to handle User Credentials (ADC)
        try:
            creds, _ = google.auth.default()
            if creds:
                creds.refresh(auth_req)
                # User Credentials (from gcloud auth application-default login) have an id_token attribute.
                if hasattr(creds, 'id_token') and creds.id_token:
                    return creds.id_token
        except google.auth.exceptions.DefaultCredentialsError:
            # Fallback to fetch_id_token
            pass
        except Exception:
            # For any other errors, also fallback
            pass

        # 2. Use fetch_id_token for Metadata Server and Service Account files
        # This handles generating ID tokens with the specific audience.
        return id_token.fetch_id_token(auth_req, audience=audience)

    @classmethod
    def _append_error_hint(cls, error: Exception) -> str:
        msg = str(error)
        if "401" in msg or "403" in msg:
            msg += cls._HINT_AUTH_PERMISSION
        
        if "HTTP" in msg:
             msg += f" For more troubleshooting steps, see: {cls._TROUBLESHOOTING_URL}"
        return msg

    @classmethod
    async def create(cls, url: str, idle_timeout: int = 60, ssl=None, enable_debug=False, debug_label="", filesystem_snapshot_name: str = None, enable_sandbox_checkpoint: bool = False, enable_idle_timeout_auto_checkpoint: bool = False, enable_auto_reconnect: bool = False, enable_sandbox_handoff: bool = False, use_google_auth: bool = False):
        """
        Creates a new sandbox session.
        """
        instance = cls("creating", enable_debug, debug_label)
        instance._enable_auto_reconnect = enable_auto_reconnect
        sanitized_url = url.rstrip('/')
        instance._base_url = sanitized_url
        instance._use_google_auth = use_google_auth
        ws_url = f"{sanitized_url}/create"
        
        token = None
        if use_google_auth:
            token = cls._get_id_token(url)
        
        ws_options = {"ssl": ssl} if ssl else {}

        try:
            instance._connection = Connection(
                url=ws_url,
                on_message=instance._on_message,
                on_error=instance._on_error,
                on_close=instance._on_close,
                should_reconnect=instance._should_reconnect,
                get_reconnect_info=instance._get_reconnect_info,
                on_reopen=instance._on_reopen,
                ws_options=ws_options,
                debug=enable_debug,
                debug_label=debug_label,
                token=token,
            )
            await instance._connection.connect()
        except Exception as e:
            msg = cls._append_error_hint(e)
            raise SandboxConnectionError(f"Failed to connect to {ws_url}: {msg}")

        create_params = {"idle_timeout": idle_timeout}
        if filesystem_snapshot_name:
            create_params["filesystem_snapshot_name"] = filesystem_snapshot_name
        if enable_sandbox_checkpoint:
            create_params["enable_checkpoint"] = enable_sandbox_checkpoint
        if enable_idle_timeout_auto_checkpoint:
            create_params["enable_idle_timeout_auto_checkpoint"] = enable_idle_timeout_auto_checkpoint
        if enable_sandbox_handoff:
            create_params["enable_sandbox_handoff"] = enable_sandbox_handoff
        await instance._send(create_params)
        
        await instance._wait_for_ready()
        return instance

    @classmethod
    async def attach(cls, url: str, sandbox_id: str, sandbox_token: str, ssl=None, enable_debug=False, debug_label="", enable_auto_reconnect: bool = False, use_google_auth: bool = False):
        """
        Attaches to an existing sandbox session.
        """
        instance = cls("attaching", enable_debug, debug_label)
        instance._enable_auto_reconnect = enable_auto_reconnect
        instance._sandbox_id = sandbox_id
        instance._sandbox_token = sandbox_token
        instance._use_google_auth = use_google_auth
        sanitized_url = url.rstrip('/')
        instance._base_url = sanitized_url
        ws_url = f"{sanitized_url}/attach/{sandbox_id}?sandbox_token={sandbox_token}"

        token = None
        if use_google_auth:
            token = cls._get_id_token(url)

        ws_options = {"ssl": ssl} if ssl else {}

        try:
            instance._connection = Connection(
                url=ws_url,
                on_message=instance._on_message,
                on_error=instance._on_error,
                on_close=instance._on_close,
                should_reconnect=instance._should_reconnect,
                get_reconnect_info=instance._get_reconnect_info,
                on_reopen=instance._on_reopen,
                ws_options=ws_options,
                debug=enable_debug,
                debug_label=debug_label,
                token=token,
            )
            await instance._connection.connect()
        except Exception as e:
            msg = cls._append_error_hint(e)
            raise SandboxConnectionError(f"Failed to connect to {ws_url}: {msg}")

        await instance._wait_for_ready()
        return instance

    def _log_debug(self, message):
        if self._enable_debug:
            label = f"[{self._debug_label}]" if self._debug_label else ""
            print(f"{label} {message}")

    async def _send(self, data: dict):
        """Sends a JSON message to the WebSocket."""
        message_str = json.dumps(data)
        if self._connection:
            await self._connection.send(message_str)

    async def _wait_for_ready(self):
        """
        Waits for the initial handshake to complete and the sandbox to be running.
        """
        await self._ready_event.wait()
        if self._error_on_ready:
            raise self._error_on_ready

    def _update_should_reconnect(self, status: SandboxEvent):
        is_fatal_error = status in [
            SandboxEvent.SANDBOX_ERROR,
            SandboxEvent.SANDBOX_NOT_FOUND,
            SandboxEvent.SANDBOX_CREATION_ERROR,
            SandboxEvent.SANDBOX_CHECKPOINT_ERROR,
            SandboxEvent.SANDBOX_RESTORE_ERROR,
            SandboxEvent.SANDBOX_DELETED,
            SandboxEvent.SANDBOX_LOCK_RENEWAL_ERROR,
            SandboxEvent.SANDBOX_PERMISSION_DENIAL_ERROR,
        ]

        if is_fatal_error:
            self._should_reconnect_internal = False
        elif status == SandboxEvent.SANDBOX_RUNNING:
            self._should_reconnect_internal = self._enable_auto_reconnect

    def _on_message(self, message_str: str):
        """Callback for handling incoming WebSocket messages."""
        message = json.loads(message_str)
        event = message.get(MessageKey.EVENT)

        # Handle noisy I/O events first and skip logging for them.
        if event in [EventType.STDOUT, EventType.STDERR]:
            if self._active_process:
                self._active_process.handle_message(message)
            return

        # For all other messages, log first.
        self._log_debug(f"Received message: {message_str}")

        # Now, dispatch the message.
        is_execution_status = (event == EventType.STATUS_UPDATE and
                               message.get(MessageKey.STATUS, "").startswith("SANDBOX_EXECUTION_") and
                               message.get(MessageKey.STATUS) !=  SandboxEvent.SANDBOX_EXECUTION_IN_PROGRESS_ERROR)

        if is_execution_status:
            if self._active_process:
                self._active_process.handle_message(message)
            return
        
        # Handle sandbox lifecycle events
        if event == EventType.SANDBOX_ID:
            self._sandbox_id = message.get(MessageKey.SANDBOX_ID)
            self._sandbox_token = message.get(MessageKey.SANDBOX_TOKEN)
            return

        if event == EventType.STATUS_UPDATE:
            status = message.get(MessageKey.STATUS)
            self._update_should_reconnect(status)
            if status == SandboxEvent.SANDBOX_RUNNING:
                self._state = "running"
                self._ready_event.set()
            elif status == SandboxEvent.SANDBOX_RESTORING:
                self._state = "restoring"
            elif status in [SandboxEvent.SANDBOX_CREATION_ERROR, SandboxEvent.SANDBOX_NOT_FOUND, SandboxEvent.SANDBOX_IN_USE, SandboxEvent.SANDBOX_RESTORE_ERROR, SandboxEvent.SANDBOX_PERMISSION_DENIAL_ERROR]:
                error = SandboxCreationError(message.get(MessageKey.MESSAGE, status))
                self._handle_error_on_ready(error)
                asyncio.create_task(self._shutdown())
            elif status in [SandboxEvent.SANDBOX_KILLED, SandboxEvent.SANDBOX_KILL_ERROR]:
                self._killed_event.set()
            elif status == SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_CREATED:
                self._state = "running"
                self._filesystem_snapshot_event.set()
            elif status == SandboxEvent.SANDBOX_FILESYSTEM_SNAPSHOT_ERROR:
                self._filesystem_snapshot_error = SandboxFilesystemSnapshotError(message.get(MessageKey.MESSAGE, status))
                self._filesystem_snapshot_event.set()
            elif status == SandboxEvent.SANDBOX_CHECKPOINTING:
                self._state = "checkpointing"
            elif status == SandboxEvent.SANDBOX_CHECKPOINTED:
                self._state = "checkpointed"
                self._checkpoint_event.set()
            elif status == SandboxEvent.SANDBOX_CHECKPOINT_ERROR:
                self._checkpoint_error = SandboxCheckpointError(message.get(MessageKey.MESSAGE, status))
                self._checkpoint_event.set()
            elif status == SandboxEvent.SANDBOX_EXECUTION_IN_PROGRESS_ERROR:
                self._state = "running"
                self._checkpoint_error = SandboxCheckpointError(message.get(MessageKey.MESSAGE, status))
                self._checkpoint_event.set()

    def _on_error(self, error: Exception):
        """Callback for handling WebSocket connection errors."""
        self._log_debug(f"An unexpected error occurred: {error}")
        self._handle_error_on_ready(error)
        asyncio.create_task(self._shutdown())

    def _on_close(self, code: int, reason: str):
        """Callback for when the WebSocket connection is closed."""
        error = SandboxConnectionError(f"Connection closed: {code} - {reason}")
        self._log_debug(f"Connection closed: {code} - {reason}")
        self._handle_error_on_ready(error)
        asyncio.create_task(self._shutdown())

    def _should_reconnect(self, code: int, reason: str) -> bool:
        return self._should_reconnect_internal and not self._is_kill_intentionally

    def _get_reconnect_info(self):
        base_url = self._base_url
        reconnect_url = f"{base_url}/attach/{self._sandbox_id}?sandbox_token={self._sandbox_token}"
        
        token = None
        if self._use_google_auth:
            # Refresh the ID token for the reconnection attempt
            token = self._get_id_token(base_url)
            
        return {"url": reconnect_url, "ws_options": self._connection.ws_options, "token": token}

    async def _on_reopen(self):
        self._log_debug("Reconnected. Sending reconnect action.")
        await self._send({"action": "reconnect"})

    async def snapshot_filesystem(self, name: str):
        """
        Triggers the creation of a filesystem snapshot.
        """
        if self._state != "running":
            raise SandboxStateError(f"Sandbox is not in a running state. Current state: {self._state}")

        self._log_debug(f"Requesting filesystem snapshot '{name}'...")
        self._state = "filesystem_snapshotting"
        self._filesystem_snapshot_event.clear()
        self._filesystem_snapshot_error = None

        await self._send({"action": "snapshot_filesystem", "name": name})
        await self._filesystem_snapshot_event.wait()
        if self._filesystem_snapshot_error:
            raise self._filesystem_snapshot_error

    async def checkpoint(self):
        """
        Triggers the creation of a checkpoint.
        """
        if self._state != "running":
            raise SandboxStateError(f"Sandbox is not in a running state. Current state: {self._state}")

        self._log_debug("Requesting checkpoint...")
        self._state = "checkpointing"
        self._checkpoint_event.clear()
        self._checkpoint_error = None

        await self._send({"action": "checkpoint"})
        await self._checkpoint_event.wait()
        if self._checkpoint_error:
            raise self._checkpoint_error
        await self._shutdown()

    async def exec(self, language: str, code: str) -> SandboxProcess:
        """
        Creates and starts a new process in the sandbox for code execution.
        """
        if self._active_process:
            raise RuntimeError("Another process is already running in this sandbox.")
        if self._state != "running":
            raise SandboxStateError(f"Sandbox is not in a running state. Current state: {self._state}")

        process = SandboxProcess(self._send, on_done=self._clear_active_process)
        self._active_process = process
        
        await process.exec(language, code)
        return process

    def _clear_active_process(self):
        self._active_process = None

    def _handle_error_on_ready(self, exc):
        if self._state in ["creating", "attaching"]:
            self._state = "failed"
            self._error_on_ready = exc
            self._ready_event.set()
            self._log_debug(f"Initialization failed: {exc}")

    async def _shutdown(self):
        async with self._shutdown_lock:
            if self._state in ["closed", "failed"]:
                return

            self._state = "closed"
            self._log_debug("State changed to closed")

            if self._active_process:
                await self._active_process.kill()
                self._active_process = None

            if self._connection:
                await self._connection.close()

    async def kill(self, timeout: float = 5.0):
        """
        Terminates the sandbox session.
        """
        if self._state in ["closed", "failed", "checkpointed"]:
            return

        self._is_kill_intentionally = True
        self._log_debug("Sending kill command to sandbox...")
        await self._send({"action": "kill_sandbox"})

        try:
            await asyncio.wait_for(self._killed_event.wait(), timeout=timeout)
            self._log_debug("Sandbox killed successfully.")
        except asyncio.TimeoutError:
            self._log_debug("Kill timeout reached. Forcing connection close.")
        finally:
            await self._shutdown()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.kill()