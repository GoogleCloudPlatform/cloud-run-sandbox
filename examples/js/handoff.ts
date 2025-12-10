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
 * This example demonstrates the sandbox handoff functionality.
 *
 * It performs the following steps:
 * 1. Creates a new sandbox (Client A) on one server instance with handoff enabled.
 * 2. Starts a long-running process in Sandbox A.
 * 3. Attaches to the same sandbox ID from a different client (Client B), potentially
 *    on a different server instance.
 * 4. The server triggers a handoff: Sandbox A is checkpointed and its connection is closed.
 * 5. Client B successfully attaches and restores the sandbox from the checkpoint.
 * 6. Client B executes a command to verify it has taken over the session.
 *
 * SERVER-SIDE REQUIREMENTS:
 * To run this example, the Cloud Run service must be deployed with a persistent
 * volume (e.g., a GCS bucket) and the `CHECKPOINT_AND_RESTORE_PATH` environment
 * variable set to the mount path of that volume.
 *
 * This script expects the URLs of your deployed Cloud Run services to be
 * set in environment variables. For a local test, you can use the same URL for both.
 *
 * The WebSocket URLs should be in the format: wss://<your-cloud-run-url>
 *
 * To run this example:
 * 1. Make sure you have ts-node and uuid installed (`npm install -g ts-node && npm install uuid @types/uuid`).
 * 2. Set the environment variables:
 *    `export CLOUD_RUN_URL="wss://your-service-a-url.run.app"
 *    `export CLOUD_RUN_URL_HANDOFF="wss://your-service-b-url.run.app"
 * 3. Run the script from the root of the repository:
 *    `npx ts-node example/js/handoff.ts`
 */
import { Sandbox } from '../../clients/js/src/sandbox';
import { v4 as uuidv4 } from 'uuid';

async function main() {
  const urlA = process.env.CLOUD_RUN_URL;
  const urlB = process.env.CLOUD_RUN_URL_HANDOFF;

  if (!urlA || !urlB) {
    console.error("Error: Please set the CLOUD_RUN_URL and CLOUD_RUN_URL_HANDOFF environment variables.");
    console.error("Example: export CLOUD_RUN_URL=\"wss://your-service-a-url.run.app\"");
    console.error("         export CLOUD_RUN_URL_HANDOFF=\"wss://your-service-b-url.run.app\"");
    return;
  }

  console.log('--- Sandbox Handoff Example ---');
  console.log(`Using URL A: ${urlA}`);
  console.log(`Using URL B (Handoff): ${urlB}`);

  let sandboxA: Sandbox | null = null;
  let sandboxB: Sandbox | null = null;

  try {
    // 1. Create the first sandbox (Sandbox A) with handoff enabled
    console.log('\n--- Step 1: Creating Sandbox A ---');
    sandboxA = await Sandbox.create(urlA, {
      enableSandboxCheckpoint: true,
      enableSandboxHandoff: true,
      enableDebug: true,
      debugLabel: 'SandboxA',
    });
    const sandboxId = sandboxA.sandboxId;
    const sandboxToken = sandboxA.sandboxToken;
    console.log(`[SandboxA] Created with ID: ${sandboxId}`);

    if (!sandboxId || !sandboxToken) {
      throw new Error("Sandbox A was created without an ID or token.");
    }

    // Add a listener to see when Sandbox A's connection is closed
    sandboxA['connection'].on('close', (code: number) => {
      console.log(`[SandboxA] Connection closed with code: ${code}. This is expected during handoff.`);
    });

    // Start a long-running process in Sandbox A to ensure it's active
    console.log('[SandboxA] Starting a long-running process...');
    const processA = await sandboxA.exec('python', 'import time; print("Process A running..."); time.sleep(5)');
    
    // We can read the output iteratively. We'll wait for the first chunk to confirm
    // the process has started, then proceed to trigger the handoff.
    for await (const chunk of processA.stdout) {
      const output = chunk.toString().trim();
      console.log(`[SandboxA] stdout: ${output}`);
      if (output.includes("Process A running...")) {
        break;
      }
    }

    // 2. Attach to the same sandbox ID from a different client/URL (Sandbox B)
    // This will trigger the handoff process on the server.
    console.log('\n--- Step 2: Attaching as Sandbox B to trigger handoff ---');
    
    sandboxB = await Sandbox.attach(urlB, sandboxId, sandboxToken, {
        enableDebug: true,
        debugLabel: 'SandboxB',
    });
    console.log(`[SandboxB] Successfully attached to sandbox ${sandboxB.sandboxId}. Handoff complete.`);

    // 3. Verify Sandbox B is working by executing a command
    console.log('\n--- Step 3: Verifying Sandbox B is operational ---');
    const processB = await sandboxB.exec('python', 'print("Hello from Sandbox B!")');
    
    const [outputB] = await Promise.all([
      processB.stdout.readAll(),
      processB.wait(),
    ]);
    console.log(`[SandboxB] stdout: ${outputB.trim()}`);
    console.log('[SandboxB] Execution finished.');

    if (outputB.includes('Hello from Sandbox B!')) {
      console.log('\n✅ Handoff successful!');
    } else {
      console.error('\n❌ Handoff failed: Did not receive expected output from Sandbox B.');
    }

  } catch (error) {
    console.error('\n❌ An error occurred during the handoff process:', error);
  } finally {
    // Cleanup: ensure both sandbox connections are killed
    if (sandboxA) {
      await sandboxA.kill();
      console.log('[SandboxA] Killed.');
    }
    if (sandboxB) {
      await sandboxB.kill();
      console.log('[SandboxB] Killed.');
    }
  }
}

main().catch(console.error);
