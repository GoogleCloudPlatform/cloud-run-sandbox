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

"""
This example demonstrates the full lifecycle of a sandbox and its processes.

To run this example, you need to have a sandbox server running.
Set the CLOUD_RUN_URL environment variable to the URL of your sandbox server.

Example:
CLOUD_RUN_URL=<URL> python3 examples/python/lifecycle.py
"""

import asyncio
import os
import sys
import ssl
import certifi

# Add the project root to the Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sandbox.sandbox import Sandbox

WEBSOCKET_URL = os.environ.get("CLOUD_RUN_URL").replace("https://", "wss://")

async def main():
    if not WEBSOCKET_URL:
        print("Error: CLOUD_RUN_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    # Create a secure SSL context using certifi's certificate bundle.
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # Create a new sandbox with debugging enabled.
    print(f"Connecting to sandbox at {WEBSOCKET_URL}...")
    sandbox = await Sandbox.create(
        WEBSOCKET_URL,
        ssl=ssl_context,
        enable_debug=True,
        debug_label="LifecycleExample",
    )

    print("Sandbox created.")

    # Execute the first process.
    print("Executing first process with unsupported language...")
    try:
        process1 = await sandbox.exec("lang-unsupported", 'echo "Process 1"; sleep 5')
        await process1.wait()
    except Exception as e:
        print(f"Caught expected error for unsupported language: {e}")
    print("Process 1 finished.")

    # Execute a second process in the same sandbox.
    print("Executing second process...")
    process2 = await sandbox.exec("bash", 'echo "Process 2"; sleep 5')
    print("Process 2 started.")

    # Intentionally kill the second process.
    print("Killing process 2...")
    await process2.kill()
    print("Process 2 killed.")

    # Wait for the second process to be fully terminated.
    await process2.wait()
    print("Process 2 finished.")

    # Execute a third, long-running process and kill it.
    print("Executing third process (long-running)...")
    py_command = (
        "import time\n"
        "for i in range(1, 11):\n"
        "    print(f'Process 3: {i}')\n"
        "    time.sleep(1)"
    )
    process3 = await sandbox.exec("python", py_command)
    print("Process 3 started.")

    # Intentionally kill the third process.
    print("Killing process 3...")
    await process3.kill()
    print("Process 3 killed.")

    # Wait for the third process to be fully terminated.
    await process3.wait()
    print("Process 3 finished.")

    # Kill the entire sandbox.
    print("Killing sandbox...")
    await sandbox.kill()
    print("Sandbox killed.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred: {e}", file=sys.stderr)