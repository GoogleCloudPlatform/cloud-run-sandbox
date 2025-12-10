# Filesystem Snapshot

This document outlines the filesystem snapshot functionality, enabling faster sandbox creation by using a pre-warmed template.

## Server Configuration

For the filesystem snapshot feature to be available, the server must be started with the following environment variables set:

-   **`FILESYSTEM_SNAPSHOT_BUCKET`**: The name of the GCS bucket where snapshot artifacts are stored.
-   **`FILESYSTEM_SNAPSHOT_MOUNT_PATH`**: The local filesystem path (on a persistent volume) where the bucket is mounted.

Both of these variables are required. If either is not set, the server will reject any client requests to create a snapshot or create a sandbox from a snapshot.

## Overview

The filesystem snapshot feature allows a client to save the filesystem of a running sandbox to a persistent volume. This snapshot can then be used as a template to create new sandboxes, which can be significantly faster than creating a new sandbox from scratch, especially if the sandbox setup involves downloading and installing dependencies.

## The Snapshot Flow

A snapshot is a non-terminal operation. Once initiated, the sandbox can continue to be used.

1.  **Client Initiates Snapshot**: The client sends a JSON message to request a snapshot.
    ```json
    {"action": "snapshot_filesystem", "name": "my-snapshot"}
    ```
2.  **Server Acknowledges**: The server immediately sends a status update to indicate that the process has begun.
    ```json
    {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATING"}
    ```
3.  **Successful Snapshot**: If the snapshot is successful, the server sends a final status update.
    ```json
    {"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATED"}
    ```

## The Create from Snapshot Flow

When a client creates a new sandbox, it can optionally specify the name of a filesystem snapshot to use.

1.  **Client Initiates Create with Snapshot**: The client sends a JSON message with the `filesystem_snapshot_name` parameter.
    ```json
    {
      "idle_timeout": 300,
      "filesystem_snapshot_name": "my-snapshot"
    }
    ```
2.  **Server Creates Sandbox from Snapshot**: The server will use the specified snapshot to create the new sandbox.
3.  **Successful Creation**: Once the sandbox is ready, the server sends the standard `SANDBOX_RUNNING` status, and the session proceeds as normal.
    ```json
    {"event": "status_update", "status": "SANDBOX_RUNNING"}
    ```

## Error Scenarios

Clients should be prepared to handle the following error flows.

### 1. General Snapshot Failure

This is a non-fatal error. It occurs if the underlying `runsc` command fails for any reason.

- **Flow**:
    1. Client sends `{"action": "snapshot_filesystem", "name": "my-snapshot"}`.
    2. Server sends `{"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_CREATING"}`.
    3. The snapshot fails.
    4. Server sends `{"event": "status_update", "status": "SANDBOX_FILESYSTEM_SNAPSHOT_ERROR"}`.
    5. Server sends a descriptive error message: `{"event": "error", "message": "..."}`.
- **Result**: The connection remains open. The client can continue to interact with the sandbox.

### 2. Create from Snapshot Failure: Snapshot Not Found

This occurs if the client tries to create a sandbox from a snapshot that does not exist.

- **Flow**:
    1. Client sends a create message with a non-existent `filesystem_snapshot_name`.
    2. The server cannot find the snapshot.
    3. Server sends `{"event": "status_update", "status": "SANDBOX_CREATION_ERROR"}`.
    4. Server sends a descriptive error message: `{"event": "error", "message": "..."}`.
- **Result**: The WebSocket connection is closed by the server with code `4000` (Application-specific error).
