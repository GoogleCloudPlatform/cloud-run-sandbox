# Cloud Run Sandbox Python Client

A Python client for interacting with the Cloud Run Sandbox service.

## Installation

This client is designed to be installed from the root of the repository as an editable package. This ensures that you have all the necessary dependencies and that the client can be easily updated.

```bash
pip install -e clients/python
```

## API

### `await Sandbox.create(url, **options)`

Creates a new sandbox instance.

-   `url` (str): The WebSocket URL of the sandbox service.
-   `**options`: Keyword arguments for configuring the sandbox.

| Option                                | Type    | Default | Description                                                                                                                                          |
| ------------------------------------- | ------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `idle_timeout`                        | `int`   | `60`    | The idle timeout in seconds. If the sandbox is idle for this duration, it will be automatically deleted or checkpointed.                               |
| `enable_sandbox_checkpoint`           | `bool`  | `False` | Enables checkpointing for the sandbox.                                                                                                               |
| `enable_idle_timeout_auto_checkpoint` | `bool`  | `False` | If `True`, the sandbox will be automatically checkpointed when the idle timeout is reached. Requires `enable_sandbox_checkpoint` to be `True`.          |
| `enable_sandbox_handoff`              | `bool`  | `False` | Enables sandbox handoff, allowing the sandbox to be reattached from a different client instance. Requires `enable_sandbox_checkpoint` to be `True`.      |
| `filesystem_snapshot_name`            | `str`   | `None`  | The name of a filesystem snapshot to load into the sandbox.                                                                                          |
| `enable_debug`                        | `bool`  | `False` | Enables debug logging.                                                                                                                               |
| `debug_label`                         | `str`   | `''`    | A label to use for debug log messages.                                                                                                               |
| `ssl`                                 | `SSLContext` | `None` | An `ssl.SSLContext` object for secure connections. It is recommended to use `ssl.create_default_context(cafile=certifi.where())`. |
| `enable_auto_reconnect`               | `bool`  | `False` | Enables automatic reconnection to the sandbox if the connection is lost.                                                                             |
| `use_google_auth`                     | `bool`  | `False` | Automatically fetches an ID token from Application Default Credentials for authentication.                                                           |

### `await Sandbox.attach(url, sandbox_id, sandbox_token, **options)`

Attaches to an existing sandbox instance.

-   `url` (str): The WebSocket URL of the sandbox service.
-   `sandbox_id` (str): The ID of the sandbox to attach to.
-   `sandbox_token` (str): The security token required to attach to the sandbox.
-   `**options`: Configuration options, same as `create` but without sandbox-specific settings.

### `await sandbox.exec(language, code)`

Executes a code snippet in the sandbox.

-   `language` (str): The language of the code snippet (e.g., `bash`, `python`).
-   `code` (str): The code to execute.

Returns a `SandboxProcess` object.

#### `SandboxProcess`

Represents a running process in the sandbox.

-   `stdout`: A `SandboxStream` for the process's standard output.
-   `stderr`: A `SandboxStream` for the process's standard error.
-   `await wait()`: Waits for the process to finish.
-   `await write_to_stdin(data)`: Writes data to the process's standard input.
-   `await kill()`: Terminates the running process in the sandbox.

### `await sandbox.kill()`

Terminates the sandbox instance.

### `await sandbox.checkpoint()`

Creates a checkpoint of the sandbox's state. The sandbox can be restored from this checkpoint later using `Sandbox.attach()`. Requires `enable_sandbox_checkpoint` to be `True`.

### `await sandbox.snapshot_filesystem(name)`

Creates a snapshot of the sandbox's filesystem.

-   `name` (str): A name for the snapshot.

## Usage Examples

### Basic Usage

```python
import asyncio
import os
import ssl
import certifi
from sandbox import Sandbox

async def main():
    # The URL should be the WebSocket endpoint of your deployed service,
    # typically "wss://<your-cloud-run-url>"
    url = os.environ.get("CLOUD_RUN_URL")
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    async with await Sandbox.create(url, ssl=ssl_context) as sandbox:
        # Execute a command
        process = await sandbox.exec('bash', "echo 'Hello from the sandbox!'")

        # Read the output
        output = await process.stdout.read_all()
        print(output)

        # Wait for the process to finish
        await process.wait()

if __name__ == "__main__":
    asyncio.run(main())
```

### Checkpointing and Attaching

```python
import asyncio
import os
import ssl
import certifi
from sandbox import Sandbox

async def main():
    url = os.environ.get("CLOUD_RUN_URL")
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # Create a sandbox with checkpointing enabled
    sandbox1 = await Sandbox.create(
        url,
        enable_sandbox_checkpoint=True,
        ssl=ssl_context
    )

    sandbox_id = sandbox1.sandbox_id
    sandbox_token = sandbox1.sandbox_token
    print(f"Created sandbox with ID: {sandbox_id}")

    # ... do some work ...

    # Checkpoint the sandbox
    await sandbox1.checkpoint()
    print('Sandbox checkpointed.')

    # Attach to the checkpointed sandbox
    async with await Sandbox.attach(url, sandbox_id, sandbox_token, ssl=ssl_context) as sandbox2:
        print('Attached to sandbox.')
        # ... continue work ...

if __name__ == "__main__":
    asyncio.run(main())
```

## Development

### Setup

It is recommended to set up a virtual environment.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e clients/python
```

### Running Tests

```bash
pytest clients/python/tests
```