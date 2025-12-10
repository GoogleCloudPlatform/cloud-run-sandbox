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
from typing import AsyncGenerator, List
from .types import SandboxOutputEvent, OutputType

logger = logging.getLogger(__name__)

class Process:
    """
    A wrapper around an asyncio subprocess that provides a consistent interface
    for creating, streaming output from, and managing the process lifecycle.

    This implementation uses background tasks to continuously drain the stdout and
    stderr pipes into a single internal queue, preventing deadlocks and providing
    a unified event stream.
    """
    def __init__(self, cmd: List[str]):
        self._cmd = cmd
        self._process: asyncio.subprocess.Process = None
        self._output_queue = asyncio.Queue()
        self._stream_tasks = []

    @property
    def is_running(self) -> bool:
        """Returns True if the process has been started and has not exited."""
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Starts the process and begins draining its output streams."""
        if self._process:
            raise RuntimeError("Process has already been started.")
        
        self._process = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        # Start background tasks to drain the pipes immediately.
        self._stream_tasks.append(
            asyncio.create_task(self._drain_pipe(self._process.stdout, OutputType.STDOUT))
        )
        self._stream_tasks.append(
            asyncio.create_task(self._drain_pipe(self._process.stderr, OutputType.STDERR))
        )

    async def _drain_pipe(self, pipe, stream_type: OutputType):
        """Reads from a pipe and pushes SandboxOutputEvents to the queue."""
        try:
            while not pipe.at_eof():
                data = await pipe.read(1024)
                if data:
                    event = SandboxOutputEvent(type=stream_type, data=data.decode("utf-8"))
                    await self._output_queue.put(event)
        except Exception as e:
            logger.debug(f"Pipe draining ended: {e}")
        finally:
            await self._output_queue.put(None) # Signal EOF for this stream

    async def stream_outputs(self) -> AsyncGenerator[SandboxOutputEvent, None]:
        """Yields events from the combined stdout/stderr stream."""
        if not self._process:
            raise RuntimeError("Process has not been started yet.")
        
        finished_streams = 0
        while finished_streams < 2:
            event = await self._output_queue.get()
            if event is None:
                finished_streams += 1
                continue
            yield event

    async def write_stdin(self, data: str) -> None:
        """Writes data to the process's stdin."""
        if self.is_running and self._process.stdin:
            self._process.stdin.write(data.encode("utf-8"))
            await self._process.stdin.drain()

    async def wait(self) -> int:
        """Waits for the process to terminate and returns its exit code."""
        if not self._process:
            raise RuntimeError("Process has not been started yet.")
        return await self._process.wait()

    async def stop(self) -> None:
        """Stops the process and cancels the stream draining tasks."""
        if self.is_running:
            self._process.terminate()
            await self._process.wait()
        
        for task in self._stream_tasks:
            task.cancel()
        self._stream_tasks.clear()
