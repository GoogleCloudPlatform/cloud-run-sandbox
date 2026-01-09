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
 * This example demonstrates the automatic reconnection feature of the Cloud Run Sandbox client.
 *
 * How this example is set up:
 * 1. We connect to a Cloud Run service that has a configured stream timeout of 5 seconds.
 *    This means the connection will be closed every 5 seconds.
 * 2. The client creates a sandbox with debug logging enabled to provide verbose output.
 * 3. After the sandbox is created, it executes a long-running script that prints a number every second for 20 seconds.
 * 4. During this 20-second execution, the 5-second stream timeout on the Cloud Run service will be exceeded.
 * 5. The client will detect the disconnection and automatically reconnect to the sandbox.
 * 6. You can observe the reconnection process in the debug logs, and the script's output will continue uninterrupted.
 *
 * Session Affinity:
 * This example also demonstrates the session affinity feature. When the client first connects to the Cloud Run service,
 * the service returns a `GAESA` cookie. The client automatically captures this cookie and includes it in all subsequent
 * requests, ensuring that reconnection attempts are routed to the same Cloud Run instance. This is crucial for
 * maintaining the sandbox session. You can observe the cookie being received and used in the debug logs.
 *
 * To run this example:
 * 1. Make sure you have ts-node installed (`npm install -g ts-node`).
 * 2. Set the environment variable for your Cloud Run service URL:
 *    `export CLOUD_RUN_URL="wss://your-service-url.run.app"
 * 3. Run the script from the root of the repository:
 *    `npx ts-node example/reconnect.ts`
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
  let sandbox: Sandbox | undefined;

  try {
    // Create a new sandbox session with debug logging and auto-reconnect enabled.
    // The `enableDebug` and `debugLabel` options provide verbose logging for the connection
    // and sandbox lifecycle, which is useful for observing the reconnection and session affinity features.
    sandbox = await Sandbox.create(url, { enableDebug: true, debugLabel: 'ReconnectExample', enableAutoReconnect: true, useGoogleAuth: true });

    console.log(`Successfully created sandbox with ID: ${sandbox.sandboxId}`);
    console.log("\nExecuting a long-running script to trigger the server's 5-second stream timeout...");
    console.log("Observe the debug logs to see the reconnection happen automatically.");

    // This script will run for 20 seconds, which is longer than the 10-second timeout.
    const longRunningScript = 'for i in $(seq 1 20); do echo "Line $i"; sleep 1; done';
    const process = await sandbox.exec("bash", longRunningScript);

    console.log("\n--- Script Output (Iterative) ---");
    for await (const chunk of process.stdout) {
      console.log(chunk.toString().trim());
    }
    console.log("---------------------------------");

    await process.wait();
    console.log('\nLong-running script finished.');

  } catch (e) {
    console.error("\nAn error occurred:", e);
  } finally {
    if (sandbox) {
      console.log('\nKilling sandbox...');
      sandbox.kill();
      console.log("Sandbox killed.");
    }
  }
}

main().catch(console.error);
