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

import pytest
import asyncio
from src.sandbox.process import Process
from src.sandbox.types import OutputType

@pytest.mark.asyncio
async def test_stream_outputs_interleaved():
    """
    Tests that stream_outputs correctly yields interleaved stdout and stderr
    events in the order they are produced.
    """
    # This script prints "out1", then "err1", then "out2", then "err2"
    script = "import sys, time; print('out1'); sys.stdout.flush(); time.sleep(0.01); print('err1', file=sys.stderr); sys.stderr.flush(); time.sleep(0.01); print('out2'); sys.stdout.flush(); time.sleep(0.01); print('err2', file=sys.stderr);"
    cmd = ["python3", "-c", script]
    process = Process(cmd)
    await process.start()

    events = []
    async for event in process.stream_outputs():
        events.append(event)
    
    await process.wait()

    assert len(events) == 4
    assert events[0]["type"] == OutputType.STDOUT
    assert events[0]["data"].strip() == "out1"
    assert events[1]["type"] == OutputType.STDERR
    assert events[1]["data"].strip() == "err1"
    assert events[2]["type"] == OutputType.STDOUT
    assert events[2]["data"].strip() == "out2"
    assert events[3]["type"] == OutputType.STDERR
    assert events[3]["data"].strip() == "err2"


@pytest.mark.asyncio
async def test_process_stop():
    """
    Tests that the stop() method correctly terminates the process.
    """
    cmd = ["bash", "-c", "echo 'start'; sleep 5; echo 'end'"]
    process = Process(cmd)
    await process.start()
    assert process.is_running

    await process.stop()
    assert not process.is_running

@pytest.mark.asyncio
async def test_process_write_stdin():
    """
    Tests that write_stdin correctly sends data to the process.
    """
    cmd = ["python3", "-c", "import sys; line = sys.stdin.readline(); print(f'read: {line.strip()}')"]
    process = Process(cmd)
    await process.start()

    await process.write_stdin("hello\n")

    output_events = []
    async for event in process.stream_outputs():
        output_events.append(event)

    stdout = "".join(e["data"] for e in output_events if e["type"] == OutputType.STDOUT)
    assert "read: hello" in stdout

    await process.wait()

@pytest.mark.asyncio
async def test_deadlock_prevention():
    """
    Tests that the process doesn't hang if the consumer is slow or only
    processes events after the process has finished.
    """
    # This command writes 10KB to stderr, then a single line to stdout.
    script = "import sys; sys.stderr.write('e' * 10000); sys.stderr.flush(); print('hello')"
    cmd = ["python3", "-c", script]
    process = Process(cmd)
    await process.start()

    # Wait for the process to finish before consuming output.
    await process.wait()

    # Now consume the output. This should not hang.
    output_events = []
    async for event in process.stream_outputs():
        output_events.append(event)

    stdout = "".join(e["data"] for e in output_events if e["type"] == OutputType.STDOUT)
    stderr = "".join(e["data"] for e in output_events if e["type"] == OutputType.STDERR)

    assert "hello" in stdout
    assert len(stderr) >= 10000
    
    await process.stop() # Clean up just in case

@pytest.mark.asyncio
async def test_runtime_errors():
    """
    Tests that methods raise RuntimeError if called out of order.
    """
    process = Process(["echo", "hello"])

    with pytest.raises(RuntimeError, match="Process has not been started yet."):
        async for _ in process.stream_outputs():
            pass

    with pytest.raises(RuntimeError, match="Process has not been started yet."):
        await process.wait()

    await process.start()
    with pytest.raises(RuntimeError, match="Process has already been started."):
        await process.start()
    
    await process.stop()

@pytest.mark.asyncio
async def test_stop_unblocks_stream_readers():
    """
    Tests that stop() unblocks consumers waiting on stream_outputs.
    """
    # This process prints output and then sleeps forever.
    cmd = ["bash", "-c", "echo 'out'; echo 'err' >&2; sleep 30"]
    process = Process(cmd)
    await process.start()
    assert process.is_running

    output_read_event = asyncio.Event()

    async def consume_outputs():
        async for _ in process.stream_outputs():
            output_read_event.set() # Signal that we've read some output
        # The loop should terminate cleanly when stop() is called.

    # Start the consumer and wait for it to read the initial output and block.
    consumer_task = asyncio.create_task(consume_outputs())
    await asyncio.wait_for(output_read_event.wait(), timeout=1)

    # Now that the consumer is blocked waiting for more data, stop the process.
    await process.stop()

    # The consumer task should now unblock and finish.
    # We await it with a timeout to prove it doesn't hang.
    await asyncio.wait_for(consumer_task, timeout=1)

    exit_code = await process.wait()
    assert exit_code != 0


