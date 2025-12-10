# Checkpoint and Restore

This document outlines the checkpoint and restore functionality, enabling stateful sandboxes that can be persisted and handed off between server instances.

> **Note:** When using checkpoint and restore in an environment like Cloud Run, where underlying hardware can vary, you may encounter an `incompatible FeatureSet` error during restore. This is due to differences in CPU features between the machine that created the checkpoint and the one restoring it. For a detailed solution, please see the [Troubleshooting](#troubleshooting) section at the end of this document.

## Server Configuration

For the checkpoint and restore feature to be available, the server must be started with the following environment variables set:

-   **`SANDBOX_CHECKPOINT_BUCKET`**: The name of the GCS bucket where checkpoint artifacts are stored.
-   **`SANDBOX_CHECKPOINT_MOUNT_PATH`**: The local filesystem path (on a persistent volume) where the bucket is mounted.

Both of these variables are required. If either is not set, the server will reject any client requests to enable checkpointing and will not attempt to restore sandboxes from disk.

### Disabling Metadata Caching

When deploying the service with a GCS volume mount, it is critical to disable metadata caching to ensure consistency and prevent race conditions in a multi-instance environment. This is achieved by adding the following option to the volume mount configuration:

`--add-volume=...,mount-options="metadata-cache-ttl-secs=0"`

The GCS FUSE layer caches file metadata by default. In a distributed system like Cloud Run, this can lead to severe issues where one server instance sees stale information about the state of a lock file or the latest checkpoint created by another instance. Disabling the cache ensures that every file operation directly queries GCS, guaranteeing that each server instance has an up-to-date view of the shared state. Without this setting, sandbox handoff and restore operations may fail intermittently and unpredictably.

## Filesystem Structure

When checkpointing is enabled for a sandbox, the following directory structure is created within the path specified by the `SANDBOX_CHECKPOINT_MOUNT_PATH` environment variable:

```
/path/to/persistent/volume/
└── {sandbox_id}/
    ├── checkpoints/
    │   ├── checkpoint_1678886400000.img
    │   ├── checkpoint_1678886460000.img
    │   └── latest
    └── metadata.json
```

-   **`{sandbox_id}/`**: A directory unique to each sandbox.
-   **`metadata.json`**: A file containing metadata about the sandbox, such as its `idle_timeout`.
-   **`checkpoints/`**: A directory containing all checkpoint files for the sandbox.
-   **`checkpoint_{timestamp}.img`**: A timestamped file representing a single checkpoint of the sandbox's state.
-   **`latest`**: A simple text file that contains the filename of the most recent checkpoint (e.g., `checkpoint_1678886460000.img`). When a sandbox is restored, the server uses this file to identify which checkpoint to load.

This structure allows for multiple checkpoints to be stored for each sandbox, with the `latest` file ensuring that the most recent checkpoint is always used for restoration.

### CPU Feature Compatibility

Cloud Run instances can run on machines with different CPU feature sets. To ensure that a sandbox checkpointed on one machine can be reliably restored on another, the server automatically determines a common set of CPU features. This set is then embedded into the sandbox's configuration using the `dev.gvisor.internal.cpufeatures` annotation. This process is handled transparently by the server whenever a checkpointable sandbox is created or restored.

## Overview

The checkpoint and restore feature allows a client to save the complete state of a sandbox, including its filesystem and running processes, to a persistent volume. The sandbox can then be destroyed from the current server instance. At a later time, a client can reconnect to a different server instance, and the sandbox will be restored from the persistent volume to the exact state it was in when it was checkpointed.

## Enabling Checkpoint Functionality

To use this feature, the client must explicitly enable it when creating a new sandbox. This is done by sending an `enable_checkpoint: true` flag in the initial configuration message over the WebSocket.

**Example Creation Message:**
```json
{
  "idle_timeout": 300,
  "enable_checkpoint": true
}
```
If the server is not configured to support checkpointing, it will respond with a `SANDBOX_CREATION_ERROR` and close the connection.

## The Checkpoint Flow

A checkpoint is a terminal operation for the current WebSocket session. Once initiated, the connection will be closed by the server, whether the operation succeeds or fails (with one exception, noted below).

1.  **Client Initiates Checkpoint**: The client sends a JSON message to request a checkpoint.
    ```json
    {"action": "checkpoint"}
    ```
2.  **Server Acknowledges**: The server immediately sends a status update to indicate that the process has begun.
    ```json
    {"event": "status_update", "status": "SANDBOX_CHECKPOINTING"}
    ```
3.  **Successful Checkpoint**: If the checkpoint is successful, the server sends a final status update and closes the connection cleanly.
    ```json
    {"event": "status_update", "status": "SANDBOX_CHECKPOINTED"}
    ```
    The WebSocket will then be closed with code `1000` (Normal Closure).

### Forced Checkpoints

In certain scenarios, such as a sandbox handoff, the server may need to checkpoint a sandbox while an execution is in progress. Simply terminating the host-side `runsc exec` process is not sufficient, as it can leave the sandboxed process in an orphaned or inconsistent state. This can lead to a corrupted checkpoint, which will fail to restore with an error such as `failed to load kernel: runtime error: invalid memory address or nil pointer dereference`.

To ensure a clean and reliable checkpoint, the server implements a more robust, two-step termination process:

1.  **Capturing the Sandboxed PID**: When a process is started with `runsc exec`, the server uses the `--internal-pid-file` flag. This flag instructs `runsc` to write the process ID (PID) as it appears *inside* the sandbox to a temporary file.

2.  **Targeted `SIGKILL`**: When a forced checkpoint is required, the server reads this captured PID and uses `runsc exec <container_id> kill -9 <pid>` to send a `SIGKILL` signal directly to the sandboxed process.

This method ensures that the executed script is forcefully and cleanly terminated from within the sandbox, leaving the container in a stable, quiescent state before the checkpoint is created. This makes the handoff process seamless and robust.

## The Restore Flow

When a client attempts to attach to a sandbox ID that is not in the server's in-memory cache, the server will attempt to restore it from the persistent volume.

1.  **Client Initiates Attach**: The client connects to the `/attach/{sandbox_id}` WebSocket endpoint.
2.  **Server Acknowledges**: If the sandbox is not in memory but a checkpoint is found on the persistent volume, the server sends a status update to indicate a restore is in progress.
    ```json
    {"event": "status_update", "status": "SANDBOX_RESTORING"}
    ```
3.  **Successful Restore**: Once the sandbox is fully restored, the server sends the standard `SANDBOX_RUNNING` status, and the session proceeds as normal.
    ```json
    {"event": "status_update", "status": "SANDBOX_RUNNING"}
    ```

## Error Scenarios

Clients should be prepared to handle the following error flows.

### 1. Checkpoint During an Active Execution

This is a recoverable error. The checkpoint will fail, but the original code execution will continue, and the WebSocket connection will remain open.

- **Flow**:
    1. Client sends `{"action": "checkpoint"}` while a command is running.
    2. Server sends `{"event": "status_update", "status": "SANDBOX_CHECKPOINTING"}`.
    3. Server detects the conflict and sends `{"event": "status_update", "status": "SANDBOX_EXECUTION_IN_PROGRESS_ERROR"}`.
    4. Server sends an error message: `{"event": "error", "message": "Cannot checkpoint while an execution is in progress."}`.
- **Result**: The connection remains open. The client can continue to interact with the sandbox and will receive the output from the original command once it completes.

### 2. General Checkpoint Failure

This is a fatal error for the session. It occurs if the underlying `runsc` command fails for any reason other than an active execution.

- **Flow**:
    1. Client sends `{"action": "checkpoint"}`.
    2. Server sends `{"event": "status_update", "status": "SANDBOX_CHECKPOINTING"}`.
    3. The checkpoint fails.
    4. Server sends `{"event": "status_update", "status": "SANDBOX_CHECKPOINT_ERROR"}`.
    5. Server sends a descriptive error message: `{"event": "error", "message": "..."}`.
- **Result**: The WebSocket connection is closed by the server with code `4000` (Application-specific error).

### 3. Restore Failure: Checkpoint Not Found

This occurs if the client tries to attach to a sandbox ID that does not exist in memory or on the persistent volume.

- **Flow**:
    1. Client connects to `/attach/{sandbox_id}`.
    2. Server sends `{"event": "status_update", "status": "SANDBOX_RESTORING"}`.
    3. The server cannot find the checkpoint files.
    4. Server sends `{"event": "status_update", "status": "SANDBOX_NOT_FOUND"}`.
- **Result**: The WebSocket connection is closed by the server with code `1011` (Server error).

### 4. Restore Failure: Corrupted Checkpoint

This occurs if the checkpoint files are present but corrupted, and the restore operation fails.

- **Flow**:
    1. Client connects to `/attach/{sandbox_id}`.
    2. Server sends `{"event": "status_update", "status": "SANDBOX_RESTORING"}`.
    3. The restore operation fails.
    4. Server sends `{"event": "status_update", "status": "SANDBOX_RESTORE_ERROR"}`.
    5. Server sends a descriptive error message: `{"event": "error", "message": "..."}`.
- **Result**: The WebSocket connection is closed by the server with code `4000` (Application-specific error).

## Troubleshooting

### "Incompatible FeatureSet: missing features..."

This error during a restore operation indicates a mismatch in CPU features between the machine where the checkpoint was created and the machine where it is being restored.

-   **Cause**: Cloud Run may schedule the checkpoint and restore operations on different physical machines with slightly different CPU capabilities.
-   **Solution**: To resolve this, you need to identify the common set of CPU features between the two environments and update the server's configuration.
    1.  **Log CPU Features**: The server is already configured to log the output of `runsc cpu-features` at the start of both `checkpoint()` and `restore()` operations. Check the server logs for messages containing "runsc cpu-features stdout".
    2.  **Identify Common Features**: Compare the feature lists from a checkpoint log and a restore log and find the intersection (the features present in both lists).
    3.  **Update Configuration**: Open `src/sandbox/gvisor.py` and replace the contents of the `common_cpu_features` string with the new comma-separated list of common features you identified.
    4.  **Redeploy**: Redeploy the server with the updated code.
