# GCS Bucket Structure and Configuration

This document outlines the recommended GCS bucket structure, metadata format, and configuration flow for enabling persistent and shareable sandboxes.

## 1. Core Principles

- **Optionality**: GCS-backed features are opt-in at sandbox creation.
- **Configurable Backends**: The storage mechanism for each type of sandbox artifact (metadata, checkpoints, snapshots) can be independently configured to use either direct GCS client access or a local filesystem path backed by a Cloud Run native GCS volume mount.
- **Separation of Concerns**: `src/sandbox/manager.py` is responsible for interpreting all storage configurations and resolving paths. Lower-level modules like `src/sandbox/gvisor.py` are consumers of these resolved paths and do not need to be aware of the configuration details.

## 2. GCS Directory Structure

While different buckets can be used for each artifact type, a clear directory structure is recommended for organization. Checkpoints and snapshots are stored at the top level to allow a new sandbox to be created from an existing artifact.

```
<bucket-name>/
├── sandboxes/
│   └── <sandbox_id>/
│       ├── lock.json         # Manages exclusive access lease
│       └── metadata.json     # The source of truth for the sandbox
│
├── sandbox_checkpoints/
│   └── <checkpoint_id>/      # Directory containing all files for a checkpoint
│       └── checkpoint.img
│
└── filesystem_snapshots/
    └── <snapshot_id>/
        └── <snapshot_id>.tar  # An archive of the sandbox filesystem
```

## 3. Metadata File (`metadata.json`)

This is the central file for a GCS-backed sandbox. It lives in the `sandboxes/<sandbox_id>/` directory.

```json
{
  "sandbox_id": "sandbox-abc-123",
  "created_timestamp": "2025-10-08T10:00:00Z",
  "idle_timeout": 300,
  "latest_sandbox_checkpoint": {
    "bucket": "company-sandbox-checkpoints",
    "path": "sandbox_checkpoints/checkpoint-xyz/"
  }
}
```

- **`bucket`**: The name of the GCS bucket where the artifact is stored.
- **`path`**: The relative path to the artifact within its bucket.

## 4. Configuration via Environment Variables

The server's storage strategy is controlled by environment variables. For each artifact type, you can provide a bucket name (for GCS client access) or a local mount path (for Cloud Run volume access).

| Artifact Type | GCS Client Mode Variable | Mounted Mode Variable | Recommended Mode | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Lock & Metadata** | `SANDBOX_METADATA_BUCKET` | `SANDBOX_METADATA_MOUNT_PATH` | **GCS Client** | Defines location for `lock.json` and `metadata.json`. GCS client is strongly recommended for atomic operations. |
| **Checkpoints** | `SANDBOX_CHECKPOINT_BUCKET` | `SANDBOX_CHECKPOINT_MOUNT_PATH` | **Mounted** | Defines location for memory checkpoints. Mounted is recommended on Cloud Run. GCS Client is a fallback. |
| **Snapshots** | `FILESYSTEM_SNAPSHOT_BUCKET` | `FILESYSTEM_SNAPSHOT_MOUNT_PATH` | **Mounted** | Defines location for filesystem snapshots. Mounted is highly recommended on Cloud Run for performance. GCS Client is a fallback. |

**Note:** For Checkpoints and Snapshots, the `_MOUNT_PATH` is the preferred configuration for Cloud Run deployments. The system supports the `_BUCKET` variables for environments where native GCS volume mounts are not used. If both are set, `_MOUNT_PATH` takes precedence.

## 5. Logic Flow and Responsibilities

1.  **Startup**: The main server process reads these environment variables to determine its storage capabilities.
2.  **Request**: A request to create or attach a sandbox with a GCS backend is received.
3.  **`manager.py`**: The sandbox manager is responsible for the entire orchestration.
    - It reads the `metadata.json` for the sandbox.
    - It resolves the path to the snapshot artifact (`.tar.gz` file).
    - If `FILESYSTEM_SNAPSHOT_MOUNT_PATH` is set, it constructs the full local path to the `.tar.gz` file.
    - If only `FILESYSTEM_SNAPSHOT_BUCKET` is set, it constructs the `gs://` URI.
4.  **`gvisor.py`**: The manager passes the resolved path to the gVisor module.
    - The gVisor module receives the path to the `.tar.gz` archive.
    - It creates a new temporary directory to serve as the sandbox rootfs.
    - It copies (from a mount path) or downloads (from a `gs://` URI) the archive.
    - It extracts the archive into the rootfs directory.
    - It starts the sandbox using this prepared directory.
