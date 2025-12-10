#!/usr/bin/env ts-node
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
 * An example of creating a pseudo-interactive shell in the sandbox.
 *
 * This script demonstrates how to pipe stdin, stdout, and stderr to create
 * a basic interactive shell experience.
 *
 * Note: This is not a true TTY and has limitations. For example, it is
 * line-buffered, so features like tab-completion will not work.
 *
 * To run this example:
 * 1. Make sure you have ts-node installed (`npm install -g ts-node`).
 * 2. Set the environment variable:
 *    `export CLOUD_RUN_URL="wss://your-service-url.run.app"
 * 3. Install the JS client for the sandbox:
 *    `npm install clients/js/`
 * 4. Run the script from the root of the repository:
 *    `ts-node example/interactive_shell.ts`
 */
import { Sandbox } from '../../clients/js/src/sandbox';

async function main() {
  const url = process.env.CLOUD_RUN_URL;
  if (!url) {
    console.error("Error: Please set the CLOUD_RUN_URL environment variable.");
    return;
  }

  let sandbox: Sandbox | undefined;
  try {
    sandbox = await Sandbox.create(url, {
      enableSandboxCheckpoint: true,
      enableDebug: true,
      debugLabel: 'ShellSandbox',
      enableAutoReconnect: true 
    });
    console.log(`Connected to sandbox: ${sandbox.sandboxId}`);
    console.log("Starting interactive shell... (type 'exit' to quit)");

    const shellProcess = await sandbox.exec('bash', "export PS1='sandbox> '; /bin/bash -i");

    // Pipe stdin
    process.stdin.on('data', (data) => {
      shellProcess.writeToStdin(data.toString());
    });

    // Pipe stdout and stderr
    shellProcess.stdout.on('data', (data) => {
      process.stdout.write(data);
    });
    shellProcess.stderr.on('data', (data) => {
      process.stderr.write(data);
    });

    // Handle process exit
    await shellProcess.wait();

  } catch (e) {
    console.error("\nAn error occurred:", e);
  } finally {
    if (sandbox) {
      sandbox.kill();
    }
    // Exit the process, otherwise the stdin listener will keep it alive.
    process.exit();
  }
}

main().catch(console.error);
