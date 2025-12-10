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
 * This example demonstrates the full lifecycle of a sandbox and its processes.
 *
 * To run this example, you need to have a sandbox server running.
 * Set the CLOUD_RUN_URL environment variable to the URL of your sandbox server.
 *
 * Example:
 * CLOUD_RUN_URL=<URL> npx ts-node example/js/lifecycle.ts
 */

import { Sandbox } from '../../clients/js/src/sandbox';

const WEBSOCKET_URL = process.env.CLOUD_RUN_URL;

async function main() {
  if (!WEBSOCKET_URL) {
    console.error('Error: CLOUD_RUN_URL environment variable is not set.');
    process.exit(1);
  }

  // Create a new sandbox with debugging enabled.
  console.log(`Connecting to sandbox at ${WEBSOCKET_URL}...`);
  const sandbox = await Sandbox.create(WEBSOCKET_URL, {
    enableDebug: true,
    debugLabel: 'LifecycleExample',
  });

  console.log('Sandbox created.');

  // Execute the first process.
  console.log('Executing first process...');
  const process1 = await sandbox.exec('bash', 'echo "Process 1"; sleep 5');
  console.log('Process 1 started.');

  // Intentionally kill the first process before it finishes.
  console.log('Killing process 1...');
  await process1.kill();
  console.log('Process 1 killed.');

  // Wait for the process to be fully terminated.
  await process1.wait();
  console.log('Process 1 finished.');

  // Execute a second process in the same sandbox.
  console.log('Executing second process...');
  const process2 = await sandbox.exec('bash', 'echo "Process 2"; sleep 5');
  console.log('Process 2 started.');

  // Intentionally kill the second process.
  console.log('Killing process 2...');
  await process2.kill();
  console.log('Process 2 killed.');

  // Wait for the second process to be fully terminated.
  await process2.wait();
  console.log('Process 2 finished.');

  // Kill the entire sandbox.
  console.log('Killing sandbox...');
  await sandbox.kill();
  console.log('Sandbox killed.');
}

main().catch(console.error);
