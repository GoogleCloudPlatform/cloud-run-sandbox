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
import shutil
import os
import logging
import datetime
from src.sandbox.gvisor import GVisorConfig
from src.sandbox.factory import make_sandbox_config, create_sandbox_instance
from src.sandbox.types import OutputType, CodeLanguage, SandboxStateEvent
from src.sandbox.interface import SandboxCreationError, SandboxExecutionInProgressError, SandboxStreamClosed, SandboxError, SandboxOperationError, SandboxState

# Check if 'runsc' is in the PATH
runsc_path = shutil.which("runsc")

_used_ip_addresses = set()

def _allocate_ip_address() -> str:
    """
    Allocates a unique IP address from the 192.168.250.0/24 subnet.
    """
    for i in range(10, 255, 2):
        ip = f"192.168.200.{i}"
        if ip not in _used_ip_addresses:
            _used_ip_addresses.add(ip)
            return ip
    raise RuntimeError("No more available IP addresses for testing.")

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_create_and_delete():
    """
    Tests that the create() method correctly creates the bundle and root directories,
    and that the delete() method cleans them up.
    """
    sandbox_id = "gvisor-test-cleanup"
    
    # Use the factory to create the sandbox instance.
    sandbox = create_sandbox_instance(sandbox_id)
    
    root_dir = sandbox._root_dir
    bundle_dir = sandbox._bundle_dir
    
    # 1. Assert that directories do not exist yet.
    assert not os.path.exists(root_dir)
    assert not os.path.exists(bundle_dir)

    try:
        # 2. Create the sandbox.
        await sandbox.create()
        assert os.path.isdir(root_dir)
        assert os.path.isdir(bundle_dir)

    finally:
        # 3. Delete the sandbox and assert that directories are removed.
        await sandbox.delete()
        assert not os.path.exists(root_dir)
        assert not os.path.exists(bundle_dir)

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_execute_python_success():
    """
    Tests that the execute() and stream_outputs() methods work correctly for a successful Python run.
    """
    sandbox_id = "gvisor-test-exec"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create() 
        
        code = "import sys; print('hello'); print('world', file=sys.stderr)"
        await sandbox.execute(CodeLanguage.PYTHON, code)

        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass

        assert len(events) == 4
        assert events[0]["type"] == "status_update"
        assert events[0]["status"] == "SANDBOX_EXECUTION_RUNNING"
        assert events[1]["type"] == OutputType.STDOUT
        assert events[1]["data"].strip() == "hello"
        assert events[2]["type"] == OutputType.STDERR
        assert events[2]["data"].strip() == "world"
        assert events[3]["type"] == "status_update"
        assert events[3]["status"] == "SANDBOX_EXECUTION_DONE"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_execute_bash_success():
    """
    Tests that the execute() and stream_outputs() methods work correctly for bash code.
    """
    sandbox_id = "gvisor-test-bash"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        code = "echo 'hello from bash' >&1; echo 'error from bash' >&2"
        await sandbox.execute(CodeLanguage.BASH, code)

        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass

        assert len(events) == 4
        assert events[0]["type"] == "status_update"
        assert events[0]["status"] == "SANDBOX_EXECUTION_RUNNING"
        assert events[1]["type"] == OutputType.STDOUT
        assert events[1]["data"].strip() == "hello from bash"
        assert events[2]["type"] == OutputType.STDERR
        assert events[2]["data"].strip() == "error from bash"
        assert events[3]["type"] == "status_update"
        assert events[3]["status"] == "SANDBOX_EXECUTION_DONE"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_execute_python_syntax_error():
    """
    Tests that stderr is captured correctly when the executed Python code has a syntax error.
    """
    sandbox_id = "gvisor-test-syntax-error"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        code = "import sys; print('hello'; print('world', file=sys.stderr)"
        await sandbox.execute(CodeLanguage.PYTHON, code)

        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass

        assert len(events) > 2
        stderr = "".join([e["data"] for e in events if e.get("type") == OutputType.STDERR])
        assert "SyntaxError" in stderr

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_execute_bash_error():
    """
    Tests that stderr is captured correctly when the executed bash script fails.
    """
    sandbox_id = "gvisor-test-bash-error"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        code = "echo 'hello from bash' >&1; not_a_command"
        await sandbox.execute(CodeLanguage.BASH, code)

        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass

        assert len(events) > 2
        stderr = "".join([e["data"] for e in events if e.get("type") == OutputType.STDERR])
        assert "command not found" in stderr

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_stream_outputs_before_execute_raises_error():
    """
    Tests that calling stream_outputs() before execute() raises a SandboxError.
    """
    sandbox_id = "gvisor-test-stream_outputs-fail"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        with pytest.raises(SandboxError, match="No process is running"):
            async for _ in sandbox.stream_outputs():
                pass
    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_stop_during_execution():
    """
    Tests that stopping the sandbox during execution correctly terminates the stream.
    """
    sandbox_id = "gvisor-test-stop"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        code = "echo 'start'; sleep 10; echo 'end'"
        await sandbox.execute(CodeLanguage.BASH, code)

        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
                if event.get("type") == OutputType.STDOUT and "start" in event["data"]:
                    await sandbox._stop()
        except SandboxStreamClosed:
            pass

        assert len(events) == 3
        assert events[0]["type"] == "status_update"
        assert events[0]["status"] == "SANDBOX_EXECUTION_RUNNING"
        assert events[1]["type"] == OutputType.STDOUT
        assert events[1]["data"].strip() == "start"
        assert events[2]["type"] == "status_update"
        assert events[2]["status"] == "SANDBOX_EXECUTION_DONE"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_stream_closes_after_execution():
    """
    Tests that the output stream closes automatically after the script finishes.
    This is a regression test for a bug where the stream would hang open.
    """
    sandbox_id = "gvisor-test-stream-close"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create() 
        
        code = "print('hello')"
        await sandbox.execute(CodeLanguage.PYTHON, code)

        all_events = []
        
        async def consume_stream():
            try:
                async for event in sandbox.stream_outputs():
                    all_events.append(event)
            except SandboxStreamClosed:
                pass

        # This test works by wrapping the stream consumption in a timeout.
        # If the stream hangs (the bug), `asyncio.wait_for` will raise a
        # `TimeoutError`, which is an unhandled exception that will cause
        # the test to fail.
        # If the stream closes correctly (the fix), the `consume_stream`
        # coroutine will complete successfully.
        await asyncio.wait_for(consume_stream(), timeout=5)

        assert len(all_events) == 3
        assert all_events[0]["status"] == "SANDBOX_EXECUTION_RUNNING"
        assert all_events[1]["data"].strip() == "hello"
        assert all_events[2]["status"] == "SANDBOX_EXECUTION_DONE"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_internet_access_host_network():
    """
    Tests that the sandbox can access the internet.
    """
    sandbox_id = "gvisor-test-internet"
    config = make_sandbox_config()
    config.network = "host"
    sandbox = create_sandbox_instance(sandbox_id, config=config)

    try:
        await sandbox.create()

        # 1. Send a request to the Internet.
        code = "python3 -c \"import urllib.request; print(urllib.request.urlopen('https://example.com').read().decode('utf-8'))\""
        await sandbox.execute(CodeLanguage.BASH, code)
        events = [event async for event in sandbox.stream_outputs()]
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "Example Domain" in stdout, "Final request to example.com failed"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_internet_access_sandbox_network():
    """
    Tests that the sandbox can access the internet.
    """
    sandbox_id = "gvisor-test-internet"
    config = make_sandbox_config()
    config.network = "sandbox"
    config.ip_address = _allocate_ip_address()
    sandbox = create_sandbox_instance(sandbox_id, config=config)

    try:
        await sandbox.create()
        
        ip_parts = config.ip_address.split('.')
        gateway_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.{int(ip_parts[3]) + 1}"

        # 1. Ping the gateway
        await sandbox.execute(CodeLanguage.BASH, f"ping -c 1 {gateway_ip}")
        events = [event async for event in sandbox.stream_outputs()]
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "1 packets transmitted, 1 received" in stdout, "Ping to gateway failed"

        # 2. Send a request to the Internet.
        code = "python3 -c \"import urllib.request; print(urllib.request.urlopen('https://example.com').read().decode('utf-8'))\""
        await sandbox.execute(CodeLanguage.BASH, code)
        events = [event async for event in sandbox.stream_outputs()]
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "Example Domain" in stdout, "Final request to example.com failed"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_writable_filesystem():
    """
    Tests that the sandbox has a writable filesystem.
    """
    sandbox_id = "gvisor-test-writable"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        code = """
        mkdir -p /test_dir
        echo "hello world" > /test_dir/test_file.txt
        cat /test_dir/test_file.txt
        """
        await sandbox.execute(CodeLanguage.BASH, code)

        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass

        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "hello world" in stdout

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_checkpoint_restore_with_network():
    """
    Tests that networking is functional after a checkpoint and restore.
    """
    sandbox_id = "gvisor-test-checkpoint-net"
    checkpoint_dir = f"/tmp/checkpoint_{sandbox_id}"
    
    # 1. Create a sandbox and verify internet access.
    config1 = make_sandbox_config()
    config1.network = "sandbox"
    config1.ip_address = _allocate_ip_address()
    sandbox1 = create_sandbox_instance(sandbox_id, config=config1)
    try:
        await sandbox1.create()
        
        # Verify internet access before checkpoint
        code = "python3 -c \"import urllib.request; print(urllib.request.urlopen('https://example.com').read().decode('utf-8'))\""
        await sandbox1.execute(CodeLanguage.BASH, code)
        events = [event async for event in sandbox1.stream_outputs()]
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "Example Domain" in stdout, "Internet access failed before checkpoint"

        os.makedirs(checkpoint_dir, exist_ok=True)
        await sandbox1.checkpoint(checkpoint_dir)
    finally:
        await sandbox1.delete()

    # 2. Restore the sandbox with a new IP and verify again.
    sandbox_id_restored = f"{sandbox_id}-restored"
    config2 = make_sandbox_config()
    config2.network = "sandbox"
    config2.ip_address = _allocate_ip_address() # Allocate a new IP
    sandbox2 = create_sandbox_instance(sandbox_id_restored, config=config2)
    sandbox2._bundle_dir = sandbox1._bundle_dir 

    try:
        await sandbox2.restore(checkpoint_dir)
        
        # Verify internet access after restore
        await sandbox2.execute(CodeLanguage.BASH, code)
        events = [event async for event in sandbox2.stream_outputs()]
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "Example Domain" in stdout, "Internet access failed after restore"
    finally:
        await sandbox2.delete()
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_multiple_executions():
    """
    Tests that multiple calls to execute() are handled correctly.
    """
    sandbox_id = "gvisor-test-multiple-exec"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        # First execution
        await sandbox.execute(CodeLanguage.PYTHON, "print('first')")
        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass
        
        assert len(events) == 3
        assert events[1]["data"].strip() == "first"

        # Second execution
        await sandbox.execute(CodeLanguage.BASH, "echo 'second'")
        events = []
        try:
            async for event in sandbox.stream_outputs():
                events.append(event)
        except SandboxStreamClosed:
            pass

        assert len(events) == 3
        assert events[1]["data"].strip() == "second"

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_reject_simultaneous_execution():
    """
    Tests that the sandbox rejects a new execution if one is already running.
    """
    sandbox_id = "gvisor-test-simultaneous"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()
        
        # Start a long-running process
        long_running_code = "import time; print('start'); time.sleep(5); print('end')"
        await sandbox.execute(CodeLanguage.PYTHON, long_running_code)

        # Try to start another execution while the first is running
        with pytest.raises(SandboxOperationError, match="An execution is already in progress"):
            await sandbox.execute(CodeLanguage.PYTHON, "print('should fail')")

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_is_attached():
    """
    Tests the is_attached property of the GVisorSandbox.
    """
    sandbox_id = "gvisor-test-is-attached"
    sandbox = create_sandbox_instance(sandbox_id)
    try:
        await sandbox.create()
        assert not sandbox.is_attached
        sandbox.is_attached = True
        assert sandbox.is_attached
    finally:
        await sandbox.delete()


@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_token():
    """
    Tests that the GVisorSandbox can create and get a sandbox token.
    """
    sandbox_id = "gvisor-test-token"
    sandbox = create_sandbox_instance(sandbox_id)
    try:
        await sandbox.create()
        await sandbox.set_sandbox_token("test-token")
        token = await sandbox.get_sandbox_token()
        assert token == "test-token"
    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_sandbox_write_stdin():
    """
    Tests that the write_stdin method correctly writes to the process's stdin.
    """
    sandbox_id = "gvisor-test-stdin"
    sandbox = create_sandbox_instance(sandbox_id)

    try:
        await sandbox.create()

        code = "line = input(); print(f'read: {line}')"
        await sandbox.execute(CodeLanguage.PYTHON, code)

        # This needs to be in a separate task because stream_outputs() will block
        # until the process is done, but the process won't be done until it
        # receives stdin.
        async def write_and_stream_outputs():
            await asyncio.sleep(0.1) # Give the process time to start
            await sandbox.write_stdin("hello\n")

            events = []
            try:
                async for event in sandbox.stream_outputs():
                    events.append(event)
            except SandboxStreamClosed:
                pass
            return events

        events = await asyncio.wait_for(write_and_stream_outputs(), timeout=5)

        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "read: hello" in stdout

    finally:
        await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_checkpoint_and_restore():
    """
    Tests the full checkpoint and restore lifecycle of a GVisorSandbox.
    """
    sandbox_id = "gvisor-test-checkpoint"
    checkpoint_dir = f"/tmp/checkpoint_{sandbox_id}"
    
    config = make_sandbox_config()
    config.network = "sandbox"
    config.ip_address = _allocate_ip_address()

    # 1. Create a sandbox and change its state
    sandbox1 = create_sandbox_instance(sandbox_id, config=config)
    try:
        await sandbox1.create()
        await sandbox1.execute(CodeLanguage.BASH, "echo 'hello' > /test.txt")
        try:
            async for _ in sandbox1.stream_outputs(): pass
        except SandboxStreamClosed: pass
        
        os.makedirs(checkpoint_dir, exist_ok=True)
        await sandbox1.checkpoint(checkpoint_dir)
    finally:
        await sandbox1.delete()

    # 2. Create a new sandbox instance and restore it.
    sandbox2 = create_sandbox_instance(sandbox_id, config=config)
    # CRITICAL: The bundle path must be the same as the original sandbox.
    sandbox2._bundle_dir = sandbox1._bundle_dir 

    try:
        await sandbox2.restore(checkpoint_dir)
        
        # 3. Verify the state was restored
        await sandbox2.execute(CodeLanguage.BASH, "cat /test.txt")
        events = []
        try:
            async for event in sandbox2.stream_outputs():
                events.append(event)
        except SandboxStreamClosed: pass
            
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "hello" in stdout
    finally:
        await sandbox2.delete()
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_checkpoint_fails_if_running():
    """
    Tests that checkpointing fails if the sandbox is in the middle of an execution.
    """
    sandbox_id = "gvisor-test-checkpoint-fail"
    sandbox = create_sandbox_instance(sandbox_id)
    try:
        await sandbox.create()
        
        # Start a long-running process
        long_running_code = "import time; print('start'); time.sleep(5); print('end')"
        await sandbox.execute(CodeLanguage.PYTHON, long_running_code)

        # Try to checkpoint while the first is running
        with pytest.raises(SandboxExecutionInProgressError, match="Cannot checkpoint while an execution is in progress"):
            await sandbox.checkpoint("/tmp/dummy_path")

    finally:
        await sandbox.delete()


@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_force_checkpoint_while_running():
    """
    Tests that a forced checkpoint terminates a running execution.
    """
    sandbox_id = "gvisor-test-force-checkpoint"
    config = make_sandbox_config()
    config.network = "sandbox"
    config.ip_address = _allocate_ip_address()
    sandbox = create_sandbox_instance(sandbox_id, config=config)
    checkpoint_dir = f"/tmp/checkpoint_{sandbox_id}"

    try:
        await sandbox.create()
        
        # Start a long-running process
        long_running_code = "import time; print('start'); time.sleep(5); print('end')"
        await sandbox.execute(CodeLanguage.PYTHON, long_running_code)

        # Give the process a moment to start running inside the sandbox
        await asyncio.sleep(0.1)
        assert sandbox._exec_process is not None
        assert sandbox._exec_process.is_running

        # A normal checkpoint should fail
        with pytest.raises(SandboxExecutionInProgressError):
            await sandbox.checkpoint(checkpoint_dir, force=False)

        # A forced checkpoint should succeed
        os.makedirs(checkpoint_dir, exist_ok=True)
        await sandbox.checkpoint(checkpoint_dir, force=True)

        # Verify that the execution process was terminated and cleared
        assert sandbox._exec_process is None

    finally:
        await sandbox.delete()
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)


@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_force_checkpoint_and_restore():
    """
    Tests that a forced checkpoint terminates a running execution and results in a usable checkpoint.
    """
    sandbox_id = "gvisor-test-force-checkpoint-restore"
    checkpoint_dir = f"/tmp/checkpoint_{sandbox_id}"
    
    config = make_sandbox_config()
    config.network = "sandbox"
    config.ip_address = _allocate_ip_address()

    # 1. Create a sandbox and start a long-running process
    sandbox1 = create_sandbox_instance(sandbox_id, config=config)
    try:
        await sandbox1.create()
        
        long_running_code = "import time; print('start'); time.sleep(10); print('end')"
        await sandbox1.execute(CodeLanguage.PYTHON, long_running_code)

        # Give the process a moment to start running
        await asyncio.sleep(0.2)
        assert sandbox1.is_execution_running

        # 2. Force a checkpoint
        os.makedirs(checkpoint_dir, exist_ok=True)
        await sandbox1.checkpoint(checkpoint_dir, force=True)

        # 3. Verify the execution was stopped
        assert not sandbox1.is_execution_running
    finally:
        await sandbox1.delete()

    # 4. Restore the sandbox and verify it's in a clean state
    sandbox2 = create_sandbox_instance(f"{sandbox_id}-restored", config=config)
    sandbox2._bundle_dir = sandbox1._bundle_dir 

    try:
        await sandbox2.restore(checkpoint_dir)
        
        # 5. Verify the state by running a simple command
        await sandbox2.execute(CodeLanguage.BASH, "echo 'hello after restore'")
        events = [event async for event in sandbox2.stream_outputs()]
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "hello after restore" in stdout
    finally:
        await sandbox2.delete()
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)


# --- State Machine Behavioral Tests ---

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_behavior_create_and_restore_guards():
    """
    Tests the behavior of calling create() or restore() in the wrong state.
    """
    sandbox = create_sandbox_instance("test-behavior-create")
    await sandbox.create()
    
    # Once running, create() and restore() should fail
    with pytest.raises(SandboxOperationError):
        await sandbox.create()
    with pytest.raises(SandboxOperationError):
        await sandbox.restore("dummy_path")
    
    await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_behavior_execute_and_checkpoint_guards():
    """
    Tests the behavior of calling execute() or checkpoint() in the wrong state.
    """
    sandbox = create_sandbox_instance("test-behavior-exec")
    
    # Before creating, execute() and checkpoint() should fail
    with pytest.raises(SandboxOperationError):
        await sandbox.execute(CodeLanguage.PYTHON, "print('hello')")
    with pytest.raises(SandboxOperationError):
        await sandbox.checkpoint("dummy_path")
        
    await sandbox.delete()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_behavior_delete_is_idempotent():
    """
    Tests that delete() can be called multiple times without error,
    demonstrating its idempotency.
    """
    try:
        # Test delete from INITIALIZED state
        sandbox1 = create_sandbox_instance("test-behavior-delete-1")
        await sandbox1.delete()
        await sandbox1.delete() # Second call should not raise an error

        # Test delete from RUNNING state
        sandbox2 = create_sandbox_instance("test-behavior-delete-2")
        await sandbox2.create()
        await sandbox2.delete()
        await sandbox2.delete() # Second call should not raise an error
    except Exception as e:
        pytest.fail(f"Calling delete() multiple times raised an unexpected exception: {e}")

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_create_failure_raises_error():
    """
    Tests that create() raises SandboxCreationError if the runsc command fails.
    """
    sandbox_id = "gvisor-test-create-fail"
    # Use a config that is likely to fail, e.g., by specifying a non-existent platform
    config = make_sandbox_config()
    config.platform = "nonexistentplatform"
    
    sandbox = create_sandbox_instance(sandbox_id, config=config)
    
    with pytest.raises(SandboxCreationError, match="Failed to create gVisor container"):
        await sandbox.create()

@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_restore_failure_raises_error():
    """
    Tests that restore() raises SandboxCreationError if the restore operation fails.
    """
    sandbox_id = "gvisor-test-restore-fail"
    sandbox = create_sandbox_instance(sandbox_id)
    
    # Attempting to restore from a non-existent checkpoint should fail
    with pytest.raises(SandboxCreationError, match="Failed to restore gVisor container"):
        await sandbox.restore("/tmp/nonexistent_checkpoint")


@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_create_with_non_existing_snapshot():
    """
    Tests that creating a sandbox with a non-existing snapshot fails.
    """
    sandbox_id = "gvisor-test-create-with-non-existing-snapshot"
    config = make_sandbox_config()
    config.filesystem_snapshot_path = "/tmp/non_existing_snapshot.tar"
    sandbox = create_sandbox_instance(sandbox_id, config=config)

    with pytest.raises(SandboxCreationError):
        await sandbox.create()


@pytest.mark.asyncio
@pytest.mark.skipif(not runsc_path, reason="runsc command not found in PATH")
async def test_gvisor_sandbox_snapshot_and_create():
    """
    Tests creating a sandbox, snapshotting it, and creating a new sandbox from the snapshot.
    """
    sandbox_id_1 = "gvisor-test-snapshot-create-1"
    snapshot_path = f"/tmp/snapshot_{sandbox_id_1}.tar"
    sandbox1 = create_sandbox_instance(sandbox_id_1)

    try:
        await sandbox1.create()
        # Create a file in the first sandbox
        await sandbox1.execute(CodeLanguage.BASH, "echo 'hello snapshot' > /test.txt")
        try:
            async for _ in sandbox1.stream_outputs(): pass
        except SandboxStreamClosed: pass

        await sandbox1.snapshot_filesystem(snapshot_path)
    finally:
        await sandbox1.delete()

    sandbox_id_2 = "gvisor-test-snapshot-create-2"
    config = make_sandbox_config()
    config.filesystem_snapshot_path = snapshot_path
    sandbox2 = create_sandbox_instance(sandbox_id_2, config=config)

    try:
        await sandbox2.create()
        # Verify the file exists in the new sandbox
        await sandbox2.execute(CodeLanguage.BASH, "cat /test.txt")
        events = []
        try:
            async for event in sandbox2.stream_outputs():
                events.append(event)
        except SandboxStreamClosed: pass
            
        stdout = "".join([e["data"] for e in events if e.get("type") == OutputType.STDOUT])
        assert "hello snapshot" in stdout
    finally:
        await sandbox2.delete()
        if os.path.exists(snapshot_path):
            os.remove(snapshot_path)


