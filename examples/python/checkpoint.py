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
import certifi
from sandbox.sandbox import Sandbox

async def main():
    """
    This example demonstrates the checkpoint and restore functionality.

    It performs the following steps:
    1. Creates a new sandbox on one server instance.
    2. Executes a command to create a file in the sandbox.
    3. Checkpoints the sandbox.
    4. Attaches to the same sandbox ID from a different client, potentially
       on a different server instance, which triggers a restore.
    5. Executes a command to verify that the file exists.

    SERVER-SIDE REQUIREMENTS:
    To run this example, the Cloud Run service must be deployed with a persistent
    volume (e.g., a GCS bucket) and the `CHECKPOINT_AND_RESTORE_PATH` environment
    variable set to the mount path of that volume.

    This script expects the URLs of your deployed Cloud Run services to be
    set in environment variables. For a local test, you can use the same URL for both.

    The WebSocket URLs should be in the format: wss://<your-cloud-run-url>

    To run this example:
    1. Set the environment variables:
       `export CLOUD_RUN_URL_CHECKPOINT="wss://your-service-a-url.run.app"
       `export CLOUD_RUN_URL_RESTORE="wss://your-service-b-url.run.app"
    2. Run the script from the root of the repository:
       `python3 example/python/checkpoint.py`
    """
    url_checkpoint = os.environ.get("CLOUD_RUN_URL_CHECKPOINT").replace("https://", "wss://")
    url_restore = os.environ.get("CLOUD_RUN_URL_RESTORE").replace("https://", "wss://")

    if not url_checkpoint or not url_restore:
        print("Error: Please set the CLOUD_RUN_URL_CHECKPOINT and CLOUD_RUN_URL_RESTORE environment variables.")
        print("Example: export CLOUD_RUN_URL_CHECKPOINT=\"wss://your-service-a-url.run.app\"")
        print("         export CLOUD_RUN_URL_RESTORE=\"wss://your-service-b-url.run.app\"")
        return

    print('--- Checkpoint and Restore Example ---')
    print(f"Using Checkpoint URL: {url_checkpoint}")
    print(f"Using Restore URL: {url_restore}")

    sandbox = None
    sandbox_id = None
    sandbox_token = None
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    try:
        # 1. Create a new sandbox
        print('\n--- Step 1: Creating Sandbox ---')
        sandbox = await Sandbox.create(
            url_checkpoint,
            enable_sandbox_checkpoint=True,
            enable_debug=True,
            debug_label='SandboxA',
            ssl=ssl_context,
            use_google_auth=True,
        )
        sandbox_id = sandbox.sandbox_id
        sandbox_token = sandbox.sandbox_token
        print(f"Successfully created sandbox with ID: {sandbox_id}")

        # 2. Write a file to the sandbox's filesystem
        filename = f"/tmp/testfile_{asyncio.get_running_loop().time()}.txt"
        content = "Hello from the original sandbox!"
        print(f"\nExecuting command to write to {filename}...")
        write_file_process = await sandbox.exec("bash", f"echo \"{content}\" > {filename}")
        await write_file_process.wait()
        print("File written successfully.")

        # 3. Checkpoint the sandbox
        print("\nCheckpointing sandbox...")
        await sandbox.checkpoint()
        print("Sandbox checkpointed successfully. The connection is now closed.")

        # 4. Attach to the checkpointed sandbox
        print(f"\nAttaching to sandbox {sandbox_id}...")
        sandbox = await Sandbox.attach(
            url_restore,
            sandbox_id,
            sandbox_token,
            enable_debug=True,
            debug_label='SandboxB',
            ssl=ssl_context,
            use_google_auth=True,
        )
        print("Successfully attached to the restored sandbox.")

        # 5. Verify the state by reading the file
        print(f"\nExecuting command to read from {filename}...")
        read_file_process = await sandbox.exec("bash", f"cat {filename}")
        stdout = await read_file_process.stdout.read_all()
        await read_file_process.wait()

        print("\n--- Restored Sandbox Output ---")
        print(f"Read from file: {stdout.strip()}")
        print("-----------------------------")

        if stdout.strip() == content:
            print("\n✅ Success: The file content was restored correctly!")
        else:
            print("\n❌ Failure: The file content did not match.")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
    finally:
        if sandbox:
            print("\nKilling sandbox...")
            await sandbox.kill()
            print("Sandbox killed.")

if __name__ == "__main__":
    asyncio.run(main())
