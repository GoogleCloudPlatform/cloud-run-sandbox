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
import time
import ssl
import certifi
from sandbox.sandbox import Sandbox

async def main():
    """
    This example demonstrates the filesystem snapshot functionality of the Cloud Run Sandbox.

    It performs the following steps:
    1. Creates a new sandbox.
    2. Executes a command to write a file to the sandbox's filesystem.
    3. Creates a snapshot of the sandbox's filesystem.
    4. Creates a new sandbox from the snapshot.
    5. Executes a command to read the file, verifying that the state was restored.

    SERVER-SIDE REQUIREMENTS:
    To run this example, the Cloud Run service must be deployed with a persistent
    volume (e.g., a GCS bucket) and the `FILESYSTEM_SNAPSHOT_PATH` environment
    variable set to the mount path of that volume.

    This script expects the URL of your deployed Cloud Run service to be
    set in the `CLOUD_RUN_URL` environment variable.

    The WebSocket URL should be in the format: wss://<your-cloud-run-url>

    To run this example:
        `CLOUD_RUN_URL=<URL> python3 examples/python/filesystem_snapshot.py`
    """
    url = os.environ.get("CLOUD_RUN_URL")
    if not url:
        print("Error: Please set the CLOUD_RUN_URL environment variable.")
        print("Example: export CLOUD_RUN_URL=\"https://your-service-url.run.app\"")
        return
    
    url = url.replace("https://", "wss://")

    print(f"Connecting to sandbox at {url}...")
    sandbox1 = None
    sandbox2 = None
    
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    try:
        # 1. Create a new sandbox
        print("Creating a new sandbox...")
        sandbox1 = await Sandbox.create(url, ssl=ssl_context)
        print(f"Successfully created sandbox with ID: {sandbox1.sandbox_id}")

        # 2. Write a file to the sandbox's filesystem
        filename = f"/tmp/testfile_{int(time.time())}.txt"
        content = "Hello from the original sandbox!"
        print(f"\nExecuting command to write to {filename}...")
        write_file_process = await sandbox1.exec("bash", f"echo \"{content}\" > {filename}")
        await write_file_process.wait()
        print("File written successfully.")

        # 3. Snapshot the sandbox filesystem
        snapshot_name = f"my-snapshot-{int(time.time())}"
        print(f"\nSnapshotting sandbox filesystem to {snapshot_name}...")
        await sandbox1.snapshot_filesystem(snapshot_name)
        print("Sandbox filesystem snapshotted successfully.")

        # 4. Create a new sandbox from the snapshot
        print(f"\nCreating a new sandbox from snapshot {snapshot_name}...")
        sandbox2 = await Sandbox.create(url, filesystem_snapshot_name=snapshot_name, ssl=ssl_context)
        print("Successfully created sandbox from snapshot.")

        # 5. Verify the state by reading the file
        print(f"\nExecuting command to read from {filename}...")
        read_file_process = await sandbox2.exec("bash", f"cat {filename}")
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
        # --- Cleanup ---
        if sandbox1:
            print("\nKilling sandbox1...")
            await sandbox1.kill()
            print("Sandbox1 killed.")
        if sandbox2:
            print("Killing sandbox2...")
            await sandbox2.kill()
            print("Sandbox2 killed.")

if __name__ == "__main__":
    asyncio.run(main())
