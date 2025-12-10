# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import os
import ssl
import sys
from pathlib import Path

import certifi

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sandbox.sandbox import Sandbox

async def run_handoff(url_a: str, url_b: str):
    """
    Demonstrates the sandbox handoff functionality.

    To run this example:
    CLOUD_RUN_URL="wss://your-service-a-url.run.app" \
    CLOUD_RUN_URL_HANDOFF="wss://your-service-b-url.run.app" \
    python3 examples/python/handoff.py
    """
    print("--- Sandbox Handoff Example ---")
    print(f"Using URL A: {url_a}")
    print(f"Using URL B (Handoff): {url_b}")

    sandbox_a = None
    sandbox_b = None
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    try:
        # 1. Create the first sandbox (Sandbox A) with handoff enabled
        print("\n--- Step 1: Creating Sandbox A ---")
        sandbox_a = await Sandbox.create(
            url=url_a,
            enable_sandbox_checkpoint=True,
            enable_sandbox_handoff=True,
            enable_debug=True,
            debug_label="SandboxA",
            ssl=ssl_context,
        )
        sandbox_id = sandbox_a.sandbox_id
        sandbox_token = sandbox_a.sandbox_token
        print(f"[SandboxA] Created with ID: {sandbox_id}")

        if not sandbox_id or not sandbox_token:
            raise RuntimeError("Sandbox A was created without an ID or token.")

        # Start a long-running process in Sandbox A
        print("[SandboxA] Starting a long-running process...")
        process_a = await sandbox_a.exec("python", 'import time; print("Process A running..."); time.sleep(5)')

        # Wait for the first chunk of output to confirm the process has started
        output_a = await process_a.stdout.read_all()
        print(f"[SandboxA] stdout: {output_a.strip()}")

        # 2. Attach to the same sandbox ID from a different client/URL (Sandbox B)
        print("\n--- Step 2: Attaching as Sandbox B to trigger handoff ---")
        sandbox_b = await Sandbox.attach(
            url=url_b,
            sandbox_id=sandbox_id,
            sandbox_token=sandbox_token,
            enable_debug=True,
            debug_label="SandboxB",
            ssl=ssl_context,
        )
        print(f"[SandboxB] Successfully attached to sandbox {sandbox_b.sandbox_id}. Handoff complete.")

        # 3. Verify Sandbox B is working by executing a command
        print("\n--- Step 3: Verifying Sandbox B is operational ---")
        process_b = await sandbox_b.exec("python", 'print("Hello from Sandbox B!")')
        
        output_b, _ = await asyncio.gather(
            process_b.stdout.read_all(),
            process_b.wait(),
        )
        print(f"[SandboxB] stdout: {output_b.strip()}")
        print("[SandboxB] Execution finished.")

        if "Hello from Sandbox B!" in output_b:
            print("\n✅ Handoff successful!")
        else:
            print("\n❌ Handoff failed: Did not receive expected output from Sandbox B.")

    except Exception as e:
        print(f"\n❌ An error occurred during the handoff process: {e}")
    finally:
        # Cleanup
        if sandbox_a:
            await sandbox_a.kill()
            print("[SandboxA] Killed.")
        if sandbox_b:
            await sandbox_b.kill()
            print("[SandboxB] Killed.")

def main():
    url_a = os.environ.get("CLOUD_RUN_URL")
    url_b = os.environ.get("CLOUD_RUN_URL_HANDOFF")

    if not url_a or not url_b:
        print("Error: Please set the CLOUD_RUN_URL and CLOUD_RUN_URL_HANDOFF environment variables.")
        print("Example: export CLOUD_RUN_URL=\"wss://your-service-a-url.run.app\"")
        print("         export CLOUD_RUN_URL_HANDOFF=\"wss://your-service-b-url.run.app\"")
        return

    # Convert http(s) URLs to ws(s) URLs
    url_a = url_a.replace("https://", "wss://").replace("http://", "ws://")
    url_b = url_b.replace("https://", "wss://").replace("http://", "ws://")

    asyncio.run(run_handoff(url_a, url_b))

if __name__ == "__main__":
    main()
