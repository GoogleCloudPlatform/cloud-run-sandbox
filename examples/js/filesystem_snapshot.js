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

/*
 * This example demonstrates the filesystem snapshot functionality.
 *
 * It performs the following steps:
 * 1. Creates a new sandbox.
 * 2. Executes a command to create a file in the sandbox.
 * 3. Takes a snapshot of the filesystem.
 * 4. Creates a NEW sandbox, initialized from that snapshot.
 * 5. Executes a command in the new sandbox to verify the file exists.
 *
 * SERVER-SIDE REQUIREMENTS:
 * To run this example, the Cloud Run service must be deployed with a persistent
 * volume (e.g., a GCS bucket) mounted. The server must be configured to use
 * this volume for storing snapshots.
 *
 * To run this example:
 * 1. Set the environment variable:
 *    `export CLOUD_RUN_URL="wss://your-service-url.run.app"
 * 2. Run the script:
 *    `node examples/js/filesystem_snapshot.js`
 */
import { Sandbox } from '../../clients/js/src/sandbox.js';

async function main() {
  const url = process.env.CLOUD_RUN_URL;
  if (!url) {
    console.error("Error: Please set the CLOUD_RUN_URL environment variable.");
    return;
  }

  let sandbox1;
  let sandbox2;
  const snapshotName = `snapshot-${Date.now()}`;

  try {
    // 1. Create the first sandbox
    console.log('\n--- Step 1: Creating Sandbox 1 ---');
    sandbox1 = await Sandbox.create(url, { useGoogleAuth: true });
    console.log(`Created Sandbox 1: ${sandbox1.sandboxId}`);

    // 2. Write a file
    const filename = `/tmp/testfile_${Date.now()}.txt`;
    const content = "Data persisted via snapshot!";
    console.log(`Writing file ${filename}...`);
    await (await sandbox1.exec("bash", `echo "${content}" > ${filename}`)).wait();

    // 3. Snapshot the filesystem
    console.log(`\n--- Step 2: Taking Filesystem Snapshot '${snapshotName}' ---`);
    await sandbox1.snapshotFilesystem(snapshotName);
    console.log("Snapshot created successfully.");

    // 4. Create a second sandbox from the snapshot
    console.log('\n--- Step 3: Creating Sandbox 2 from Snapshot ---');
    sandbox2 = await Sandbox.create(url, {
      filesystemSnapshotName: snapshotName,
      useGoogleAuth: true,
    });
    console.log(`Created Sandbox 2: ${sandbox2.sandboxId}`);

    // 5. Verify file exists in second sandbox
    console.log(`\n--- Step 4: Verifying File in Sandbox 2 ---`);
    const readProcess = await sandbox2.exec("bash", `cat ${filename}`);
    const stdout = await readProcess.stdout.readAll();
    await readProcess.wait();

    console.log(`Read content: "${stdout.trim()}"`);

    if (stdout.trim() === content) {
      console.log("✅ Success: File content matches!");
    } else {
      console.error("❌ Failure: File content does not match.");
    }

  } catch (e) {
    console.error("Error:", e);
  } finally {
    if (sandbox1) await sandbox1.kill();
    if (sandbox2) await sandbox2.kill();
  }
}

main().catch(console.error);