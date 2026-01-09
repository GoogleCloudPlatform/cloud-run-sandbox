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
from sandbox import Sandbox

async def main():
    """
    A simple example demonstrating how to use the Cloud Run Sandbox Python client.
    
    This script expects the URL of your deployed Cloud Run service to be
    set in the `CLOUD_RUN_URL` environment variable.
    
    The WebSocket URL should be in the format: wss://<your-cloud-run-url>.

    Ensure you also installed the Sandbox client for Python before running this.
    
    Example:
        CLOUD_RUN_URL="wss://sandbox-xxxxxxxxxx-uc.a.run.app" python3 example/python/basic.py
    """
    url = os.environ.get("CLOUD_RUN_URL").replace("https://", "wss://")
    if not url:
        print("Error: Please set the CLOUD_RUN_URL environment variable.")
        print("Example: export CLOUD_RUN_URL=\"wss://your-service-url.run.app\"")
        return

    print(f"Connecting to sandbox at {url}...")
    
    # Create a secure SSL context using certifi's certificate bundle.
    # This is the recommended approach for ensuring secure connections.
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    try:
        # Create a new sandbox session
        # Set `authenticated=True` to automatically fetch an ID token from Application Default Credentials.
        sandbox = await Sandbox.create(url, ssl=ssl_context, enable_debug=True, debug_label="client_example", use_google_auth=True)
        print(f"Successfully created sandbox with ID: {sandbox.sandbox_id}")

        # Execute a bash command
        print("\nExecuting command: echo 'Hello from bash!'")
        process = await sandbox.exec("bash", "echo 'Hello from bash!'")

        # Read the output streams
        stdout = await process.stdout.read_all()
        stderr = await process.stderr.read_all()
        
        # Wait for the process to finish before starting the next one.
        await process.wait()
        
        print("\n--- Bash Output ---")
        if stdout:
            print(f"STDOUT:\n{stdout}")
        if stderr:
            print(f"STDERR:\n{stderr}")
        print("-------------------")

        # Execute a python command demonstrating streaming I/O
        py_command = (
            "import time\n"
            "for i in range(1, 6):\n"
            "    print(i)\n"
            "    time.sleep(1)"
        )
        print("\nExecuting streaming Python command:")
        print(py_command)
        process = await sandbox.exec("python", py_command)

        # Read the output streams using async iteration
        print("\n--- Python Streaming Output ---")
        print("STDOUT:")
        async for chunk in process.stdout:
            print(chunk, end="", flush=True)
        
        print("\nSTDERR:")
        async for chunk in process.stderr:
            print(chunk, end="", flush=True)
        print("\n-----------------------------")

    except Exception as e:
        print(f"\nAn error occurred: {e}")
    
    finally:
        # Terminate the sandbox session
        if 'sandbox' in locals() and sandbox:
            print("\nTerminating sandbox...")
            await sandbox.kill()
            print("Sandbox terminated.")

if __name__ == "__main__":
    # This script needs the `clients/python` directory to be in the PYTHONPATH
    # You can run it from the root of the repository like this:
    # PYTHONPATH=./clients/python/src python3 example/client_example.py
    asyncio.run(main())
