# Cloud Run Sandbox TypeScript Client

A TypeScript client for interacting with the Cloud Run Sandbox service.

## Installation

```bash
npm install
```

## API

### `Sandbox.create(url, options)`

Creates a new sandbox instance.

-   `url` (string): The WebSocket URL of the sandbox service.
-   `options` (object, optional): Configuration options for the sandbox.

| Option                            | Type      | Default | Description                                                                                                                                          |
| --------------------------------- | --------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `idleTimeout`                     | `number`  | `60`    | The idle timeout in seconds. If the sandbox is idle for this duration, it will be automatically deleted or checkpointed.                               |
| `enableSandboxCheckpoint`         | `boolean` | `false` | Enables checkpointing for the sandbox.                                                                                                               |
| `enableIdleTimeoutAutoCheckpoint` | `boolean` | `false` | If `true`, the sandbox will be automatically checkpointed when the idle timeout is reached. Requires `enableSandboxCheckpoint` to be `true`.          |
| `enableSandboxHandoff`            | `boolean` | `false` | Enables sandbox handoff, allowing the sandbox to be reattached from a different client instance. Requires `enableSandboxCheckpoint` to be `true`.      |
| `filesystemSnapshotName`          | `string`  |         | The name of a filesystem snapshot to load into the sandbox.                                                                                          |
| `enableDebug`                     | `boolean` | `false` | Enables debug logging.                                                                                                                               |
| `debugLabel`                      | `string`  | `''`    | A label to use for debug log messages.                                                                                                               |
| `wsOptions`                       | `object`  |         | [ws client options](https://github.com/websockets/ws/blob/master/doc/ws.md#new-websocketaddress-protocols-options) to pass to the WebSocket client. |
| `enableAutoReconnect`             | `boolean` | `false` | Enables automatic reconnection to the sandbox if the connection is lost.                                                                             |
| `useGoogleAuth`                   | `boolean` | `false` | Automatically fetches an ID token from Application Default Credentials for authentication.                                                           |

### `Sandbox.attach(url, sandboxId, sandboxToken, options)`

Attaches to an existing sandbox instance.

-   `url` (string): The WebSocket URL of the sandbox service.
-   `sandboxId` (string): The ID of the sandbox to attach to.
-   `sandboxToken` (string): The security token required to attach to the sandbox.
-   `options` (object, optional): Configuration options, same as `create` but without sandbox-specific settings.

### `sandbox.exec(language, code)`

Executes a code snippet in the sandbox.

-   `language` (string): The language of the code snippet (e.g., `bash`, `python`).
-   `code` (string): The code to execute.

Returns a `SandboxProcess` object.

#### `SandboxProcess`

Represents a running process in the sandbox.

-   `stdout`: A readable stream for the process's standard output.
-   `stderr`: A readable stream for the process's standard error.
-   `wait()`: Returns a promise that resolves when the process has finished.
-   `writeToStdin(data)`: Writes data to the process's standard input.
-   `kill()`: Terminates the running process in the sandbox.

### `sandbox.kill()`

Terminates the sandbox instance.

### `sandbox.checkpoint()`

Creates a checkpoint of the sandbox's state. The sandbox can be restored from this checkpoint later using `Sandbox.attach()`. Requires `enableSandboxCheckpoint` to be `true`.

### `sandbox.snapshotFilesystem(name)`

Creates a snapshot of the sandbox's filesystem.

-   `name` (string): A name for the snapshot.

## Usage Examples

### Basic Usage

```typescript
import { Sandbox } from './src/sandbox';

async function main() {
  // The URL should be the WebSocket endpoint of your deployed service,
  // typically "wss://<your-cloud-run-url>"
  const sandbox = await Sandbox.create('wss://<CLOUD_RUN_URL>');

  // Execute a command
  const process = await sandbox.exec('bash', "echo 'Hello from the sandbox!'");

  // Read the output
  for await (const chunk of process.stdout) {
    console.log(chunk.toString());
  }

  // Wait for the process to finish
  await process.wait();

  // Kill the sandbox
  await sandbox.kill();
}

main().catch(console.error);
```

### Checkpointing and Attaching

```typescript
import { Sandbox } from './src/sandbox';

async function main() {
  // Create a sandbox with checkpointing enabled
  const sandbox1 = await Sandbox.create('wss://<CLOUD_RUN_URL>', {
    enableSandboxCheckpoint: true,
  });

  const sandboxId = sandbox1.sandboxId;
  console.log(`Created sandbox with ID: ${sandboxId}`);

  // ... do some work ...

  // Checkpoint the sandbox
  await sandbox1.checkpoint();
  console.log('Sandbox checkpointed.');

  // Attach to the checkpointed sandbox
  const sandbox2 = await Sandbox.attach('wss://<CLOUD_RUN_URL>', sandboxId);
  console.log('Attached to sandbox.');

  // ... continue work ...
  await sandbox2.kill();
}
```

## Development

### Setup
```bash
npm install
```

### Running Tests
```bash
npm test
```
