/**
 * Copyright 2025 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * This example demonstrates the filesystem snapshot functionality of the Cloud Run Sandbox.
 *
 * It performs the following steps:
 * 1. Creates a new sandbox.
 * 2. Executes a command to write a file to the sandbox's filesystem.
 * 3. Creates a snapshot of the sandbox's filesystem.
 * 4. Creates a new sandbox from the snapshot.
 * 5. Executes a command to read the file, verifying that the state was restored.
 *
 * SERVER-SIDE REQUIREMENTS:
 * To run this example, the Cloud Run service must be deployed with a persistent
 * volume (e.g., a GCS bucket) and the `FILESYSTEM_SNAPSHOT_PATH` environment
 * variable set to the mount path of that volume.
 *
 * This script expects the URL of your deployed Cloud Run service to be
 * set in the `CLOUD_RUN_URL` environment variable.
 *
 * The WebSocket URL should be in the format: wss://<your-cloud-run-url>
 *
 * To run this example:
 * 1. Make sure you have ts-node installed (`npm install -g ts-node`).
 * 2. Set the environment variable:
 *    `export CLOUD_RUN_URL="wss://your-service-url.run.app"
 * 3. Run the script from the root of the repository:
 *    `npx ts-node example/js/filesystem_snapshot.ts`
 */
import { Sandbox } from '../../clients/js/src/sandbox';

async function main() {
  const url = process.env.CLOUD_RUN_URL;
  if (!url) {
    console.error("Error: Please set the CLOUD_RUN_URL environment variable.");
    console.error("Example: export CLOUD_RUN_URL=\"wss://your-service-url.run.app\"");
    return;
  }

  console.log(`Connecting to sandbox at ${url}...`);
  let sandbox1: Sandbox | undefined;
  let sandbox2: Sandbox | undefined;

  try {
    // 1. Create a new sandbox
    console.log("Creating a new sandbox...");
    sandbox1 = await Sandbox.create(url, { useGoogleAuth: true });
    console.log(`Successfully created sandbox with ID: ${sandbox1.sandboxId}`);

    // 2. Write a file to the sandbox's filesystem
    const filename = `/tmp/testfile_${Date.now()}.txt`;
    const content = "Hello from the original sandbox!";
    console.log(`\nExecuting command to write to ${filename}...`);
    const writeFileProcess = await sandbox1.exec("bash", `echo "${content}" > ${filename}`);
    await writeFileProcess.wait();
    console.log("File written successfully.");

    // 3. Snapshot the sandbox filesystem
    const snapshotName = `my-snapshot-${Date.now()}`;
    console.log(`\nSnapshotting sandbox filesystem to ${snapshotName}...`);
    await sandbox1.snapshotFilesystem(snapshotName);
    console.log("Sandbox filesystem snapshotted successfully.");

    // 4. Create a new sandbox from the snapshot
    console.log(`\nCreating a new sandbox from snapshot ${snapshotName}...`);
    sandbox2 = await Sandbox.create(url, { filesystemSnapshotName: snapshotName, useGoogleAuth: true });
    console.log("Successfully created sandbox from snapshot.");

    // 5. Verify the state by reading the file
    console.log(`\nExecuting command to read from ${filename}...`);
    const readFileProcess = await sandbox2.exec("bash", `cat ${filename}`);
    const stdout = await readFileProcess.stdout.readAll();
    await readFileProcess.wait();

    console.log("\n--- Restored Sandbox Output ---");
    console.log(`Read from file: ${stdout.trim()}`);
    console.log("-----------------------------");

    if (stdout.trim() === content) {
      console.log("\n✅ Success: The file content was restored correctly!");
    } else {
      console.error("\n❌ Failure: The file content did not match.");
    }
  } catch (e) {
    console.error("\nAn error occurred:", e);
  } finally {
    // --- Cleanup ---
    if (sandbox1) {
      console.log("\nKilling sandbox1...");
      sandbox1.kill();
      console.log("Sandbox1 killed.");
    }
    if (sandbox2) {
      console.log("Killing sandbox2...");
      sandbox2.kill();
      console.log("Sandbox2 killed.");
    }
  }
}

main().catch(console.error);
