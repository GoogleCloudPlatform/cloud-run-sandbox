# Cloud Run Sandbox

This project provides a web server for executing arbitrary code (such as Python and Bash) in a secure sandboxed environment on Google Cloud Run. It leverages gVisor (`runsc`) for strong isolation and supports advanced features like stateful sessions through memory checkpoint/restore and efficient environment duplication via filesystem snapshots.

## 1. Deployment

To deploy this service to Cloud Run, you will need to have the [`gcloud` CLI installed and authenticated](https://docs.cloud.google.com/sdk/docs/install-sdk).

Then run the following command from any directoryt to deploy the pre-built container:

```bash
gcloud run deploy sandbox \
  --image=us-docker.pkg.dev/cloudrun/container/sandbox:latest \
  --region=us-central1 \
  --allow-unauthenticated \
  --execution-environment=gen2 \
  --cpu 8 \
  --memory 32Gi \
  --concurrency=1 \
  --session-affinity \
  --project=<YOUR_PROJECT_ID>
```

Replace `<YOUR_PROJECT_ID>` with your Google Cloud project ID.

Alternatively, deploy the servcie from source by cloning this repository and running the `gcloud` command above by replacing `--image=us-docker.pkg.dev/cloudrun/container/sandbox:latest` with `--source=.`.

## 2. Using the Client Libraries

The most convenient way to interact with the sandbox is by using one of the client libraries.

For each of these examples, ensure you first install the respective client in the clients/ folder. For example, `npm install clients/js/` for Typescript.

### TypeScript

Here is a simple example of how to connect to the sandbox, execute a command, and print its output:

```typescript
import { Sandbox } from './clients/js/src/sandbox';

// Replace `https` with `wss` of the Cloud Run service URL.
const url = "wss://<YOUR_SERVICE_URL>";
const sandbox = await Sandbox.create(url);

// Execute a command
const process = await sandbox.exec('bash', "echo 'Hello from the sandbox!'");

// Read the output
const stdout = await process.stdout.readAll();
console.log(`STDOUT: ${stdout}`);

// Clean up the sandbox session
sandbox.kill();
```

For a more detailed example, please see `examples/js/basic.ts`.

### Python

Here is a simple example of how to connect to the sandbox, execute a command, and print its output:

To install the Python client, run the following command from the root of the repository:

```python
# Create a sandbox
sandbox = await Sandbox.create(url, ssl=ssl_context) 

# Execute a command
process = await sandbox.exec('bash', "echo 'Hello from the sandbox!'")

# Read the output
output = await process.stdout.read_all()
print(output)

# Wait for the process to finish
await process.wait()

# Clean up the sandbox session
await sandbox.kill()
```

For a more detailed example, please see `examples/python/basic.py`.

## 3. Checkpoint and Restore

The sandbox supports stateful sessions through a checkpoint and restore mechanism. This allows a client to save the complete state of a sandbox (including its filesystem and running processes) to a persistent volume, disconnect, and later reconnect to a new server instance, restoring the sandbox to its exact previous state.

### Server Configuration

To enable this feature, the Cloud Run service must be deployed with a persistent volume and specific environment variables:
  - `SANDBOX_METADATA_MOUNT_PATH`
  - `SANDBOX_METADATA_BUCKET`
  - `SANDBOX_CHECKPOINT_MOUNT_PATH`
  - `SANDBOX_CHECKPOINT_BUCKET`

Please see [Checkpoint and Restore](docs/checkpoint_and_restore.md) for more details.

1.  **Create a GCS Bucket**: First, ensure you have a Google Cloud Storage bucket to store the checkpoints.
2.  **Deploy with Volume Mount**: Deploy the service with the GCS bucket mounted as a volume.

Here is an example `gcloud` command:

```bash
PROJECT_ID=<YOUR_PROJECT_ID>
BUCKET_NAME=<YOUR_BUCKET_NAME>

gcloud run deploy sandbox --source . \
  --project=${PROJECT_ID} \
  --region=us-central1 \
  --allow-unauthenticated \
  --execution-environment=gen2 \
  --cpu 8 \
  --memory 32Gi \
  --concurrency=1 \
  --session-affinity \
  --add-volume=name=gcs-volume,type=cloud-storage,mount-options="metadata-cache-ttl-secs=0",bucket=${BUCKET_NAME} \
  --add-volume-mount=volume=gcs-volume,mount-path=/mnt/gcs\
  --set-env-vars='SANDBOX_METADATA_MOUNT_PATH=/mnt/gcs' \
  --set-env-vars='SANDBOX_METADATA_BUCKET=${BUCKET_NAME}' \
  --set-env-vars='SANDBOX_CHECKPOINT_MOUNT_PATH=/mnt/gcs' \
  --set-env-vars='SANDBOX_CHECKPOINT_BUCKET=${BUCKET_NAME}'
```

Replace `<YOUR_PROJECT_ID>` and `<YOUR_BUCKET_NAME>` accordingly. If these are not configured, the server will reject any client requests to use the checkpointing feature.

### Example Usage

Here is a simplified example of the checkpoint/restore flow:

```typescript
// 1. Create a sandbox with checkpointing enabled
const sandbox1 = await Sandbox.create(url, { enableSandboxCheckpoint: true });
const sandboxId = sandbox1.sandboxId;

// 2. Checkpoint the sandbox
await sandbox1.checkpoint();

// 4. At a later time, attach to the sandbox to restore its state
const sandbox2 = await Sandbox.attach(url, sandboxId);
```

For a complete, runnable demonstration, please see the example file: `examples/js/checkpoint.ts`.

## 4. Filesystem Snapshot

The sandbox supports creating a filesystem snapshot from a running sandbox. This allows a client to create a new sandbox from a snapshot, which can be faster than creating a new sandbox from scratch.

### Server Configuration

To enable this feature, the Cloud Run service must be deployed with a persistent volume and specific environment variables of `FILESYSTEM_SNAPSHOT_PATH` and `FILESYSTEM_SNAPSHOT_BUCKET`.

Please see [Filesystem Snapshot](docs/filesystem_snapshot.md) for more details.

1.  **Create a GCS Bucket**: First, ensure you have a Google Cloud Storage bucket to store the snapshots.
2.  **Deploy with Volume Mount**: Deploy the service with the GCS bucket mounted as a volume.

Here is an example `gcloud` command:

```bash
PROJECT_ID=<YOUR_PROJECT_ID>
BUCKET_NAME=<YOUR_BUCKET_NAME>

gcloud run deploy sandbox --source . \
  --project=${PROJECT_ID} \
  --region=us-central1 \
  --allow-unauthenticated \
  --execution-environment=gen2 \
  --cpu 8 \
  --memory 32Gi \
  --concurrency=1 \
  --session-affinity \
  --add-volume=name=gcs-volume,type=cloud-storage,mount-options="metadata-cache-ttl-secs=0",bucket=${BUCKET_NAME} \
  --add-volume-mount=volume=gcs-volume,mount-path=/mnt/gcs \
  --set-env-vars='FILESYSTEM_SNAPSHOT_MOUNT_PATH=/mnt/gcs' \
  --set-env-vars='FILESYSTEM_SNAPSHOT_BUCKET=${BUCKET_NAME}'
```

Replace `<YOUR_PROJECT_ID>` and `<YOUR_BUCKET_NAME>` accordingly. If these are not configured, the server will reject any client requests to use the filesystem snapshot feature.

### Example Usage

Here is a simplified example of the filesystem snapshot flow:

```typescript
// 1. Create a sandbox
const sandbox1 = await Sandbox.create(url);

// 2. Snapshot the sandbox filesystem
await sandbox1.snapshotFilesystem('my-snapshot');

// 3. At a later time, create a new sandbox from the snapshot
const sandbox2 = await Sandbox.create(url, { filesystemSnapshotName: 'my-snapshot' });
```

For a complete, runnable demonstration, please see the example file: `examples/js/filesystem_snapshot.ts`.

## 5. Executing Python or Bash Code via HTTP (One-off testing)

To execute a Python or Bash script with HTTP, you can send a POST request to the `/execute`
endpoint with the content of the script as the request body, and `language=[python|bash]` as a
query parameter.

For example, to execute the `test_hello.py` script in `examples` directory:

```bash
curl -s -X POST -H "Content-Type: text/plain" --data-binary @examples/test_hello.py https://<YOUR_SERVICE_URL>/execute?language=python
```

For bash scripts,

```bash
curl -s -X POST -H "Content-Type: text/plain" --data "echo 'hello from bash'" https://<YOUR_SERVICE_URL>/execute?language=bash
```

Replace `<YOUR_SERVICE_URL>` with the URL of your deployed Cloud Run service. The output of the script will be available in the Cloud Run logs.

## 6. Limitation

Currently, the sandbox environment does not support importing non-standard Python libraries.

A simple way is workaround is to update the Dockerfile to install the libraries needed.

**TODO:** Add support for installing and using third-party libraries.

The Google Cloud Platform Terms of Service (available at https://cloud.google.com/terms/) and the Data Processing and Security Terms (available at https://cloud.google.com/terms/data-processing-terms) do not apply to any component of the Cloud Run Sandbox software.
