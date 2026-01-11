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
 * A simple example demonstrating how to use the Cloud Run Sandbox TypeScript client. 
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
 *    `npx ts-node examples/js/basic.ts`
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
    // Create a new sandbox session, with a 10-second timeout.
    const timeoutPromise = new Promise<Sandbox>((_, reject) => 
      setTimeout(() => reject(new Error('Connection timed out after 10 seconds')), 10000)
    );
    
    sandbox = await Promise.race([
      // Set `useGoogleAuth: true` to automatically fetch an ID token from Application Default Credentials.
      Sandbox.create(url, { useGoogleAuth: true }),
      timeoutPromise
    ]);

    console.log(`Successfully created sandbox with ID: ${sandbox.sandboxId}`);

    // Execute a bash command
    console.log("\nExecuting command: echo 'Hello from bash!'");
    const process = await sandbox.exec("bash", "echo 'Hello from bash!'");

    // Concurrently read streams and wait for the process to complete
    const [stdout, stderr] = await Promise.all([
      process.stdout.readAll(),
      process.stderr.readAll(),
      process.wait(),
    ]);
    
    console.log("\n--- Bash Output ---");
    if (stdout) {
      console.log(`STDOUT:\n${stdout}`);
    }
    if (stderr) {
      console.log(`STDERR:\n${stderr}`);
    }
    console.log("-------------------");
    console.log('Process finished.');

    // Iterative read example
    console.log("\nExecuting Python script and reading output iteratively...");
    const pythonScript = `
import time
for i in range(5):
    print(f"Line {i+1}")
    time.sleep(0.5)
`;
    const pythonProcess = await sandbox.exec("python", pythonScript);

    console.log("\n--- Python Output (Iterative) ---");
    for await (const chunk of pythonProcess.stdout) {
      console.log(chunk.toString());
    }
    console.log("---------------------------------");
    await pythonProcess.wait();
    console.log('Python script finished.');


    // Stdin example
    console.log("\nExecuting Python script and writing to stdin...");
    const stdinScript = `
import sys
print("Python script waiting for 2 lines from stdin...")
for i in range(2):
    line = sys.stdin.readline()
    print(f"Read from stdin: {line.strip()}")
print("Python script finished.")
`;
    const stdinProcess = await sandbox.exec("python", stdinScript);

    // Write to stdin after a short delay
    setTimeout(() => stdinProcess.writeToStdin("Hello from the client!\n"), 500);
    setTimeout(() => stdinProcess.writeToStdin("Another line\n"), 1000);

    console.log("\n--- Python Stdin Output ---");
    const [stdin_stdout] = await Promise.all([
      stdinProcess.stdout.readAll(),
      stdinProcess.wait(),
    ]);
    console.log(`STDOUT:\n${stdin_stdout}`);
    console.log("--------------------------");
    console.log('Python script with stdin finished.');


  } catch (e) {
    console.error("\nAn error occurred:", e);
  } finally {
    if (sandbox) {
      // Kill the sandbox
      sandbox.kill();
      console.log("Sandbox killed.");
    }
  }
}

main().catch(console.error);
