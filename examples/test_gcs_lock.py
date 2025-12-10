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
This is an example script to demonstrate the usage of the GCSLock for
distributed locking. It simulates two processes ("workers") competing for the
same lock.

**Prerequisites:**

1.  **Google Cloud Authentication:** You must be authenticated with Google Cloud.
    The simplest way is to use the gcloud CLI:
    ```bash
    gcloud auth application-default login
    ```

2.  **GCS Bucket:** You need a GCS bucket. You can create one with `gsutil`:
    ```bash
    gsutil mb gs://your-unique-bucket-name
    ```

3.  **Environment Variable:** Set the `GCS_BUCKET` environment variable to the
    name of your bucket:
    ```bash
    export GCS_BUCKET="your-unique-bucket-name"
    ```

**To Run:**

```bash
python3 example/test_gcs_lock.py
```

**Expected Output:**

You will see one worker acquire the lock and the other one fail with a
`LockContentionError`. This demonstrates that the lock is working correctly.
The second part of the script shows that if the lock is not contended, both
workers can acquire it sequentially.
"""
import asyncio
import os
import uuid
import logging

# Configure logging to see the lock's internal messages
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from src.sandbox.lock.gcs import GCSLock, LockContentionError, LockTimeoutError

# --- Test Configuration ---
# The owner ID should be a unique identifier for the process holding the lock.
# In a real application, this could be a pod name, a process ID, or a random UUID.
OWNER_ID_1 = f"worker-1-{uuid.uuid4()}"
OWNER_ID_2 = f"worker-2-{uuid.uuid4()}"


async def worker(name: str, lock: GCSLock):
    """A simple worker that tries to acquire a lock, holds it, and releases it."""
    logging.info(f"[{name}] Attempting to acquire lock...")
    try:
        # `acquire()` will wait for the lock if it's held. If another process
        # is already waiting, it will raise LockContentionError immediately.
        await lock.acquire(timeout_sec=20)
        logging.info(f"[{name}] Lock acquired!")

        # Simulate doing some work while holding the lock
        await asyncio.sleep(3)

    except LockContentionError as e:
        logging.warning(f"[{name}] Could not acquire lock: {e}")
    except LockTimeoutError:
        logging.warning(f"[{name}] Timed out waiting for the lock to be released.")
    except Exception:
        logging.exception(f"[{name}] An unexpected error occurred.")
    finally:
        # It's crucial to always release the lock.
        logging.info(f"[{name}] Releasing lock...")
        await lock.release()
        logging.info(f"[{name}] Lock released.")


async def main():
    """
    Sets up and runs the lock contention simulation.
    """
    gcs_bucket = os.environ.get("GCS_BUCKET")
    if not gcs_bucket:
        print("ERROR: The GCS_BUCKET environment variable is not set.")
        print("Please set it to the name of your Google Cloud Storage bucket.")
        return

    # --- Part 1: Contention Test ---
    logging.info("--- Running contention test ---")
    logging.info("Two workers will try to acquire the same lock at the same time.")
    
    lock_name_contention = f"locks/demo-lock-{uuid.uuid4()}.lock"
    gcs_path_contention = f"gs://{gcs_bucket}/{lock_name_contention}"
    logging.info(f"Using lock path for contention test: {gcs_path_contention}")

    lock1_contention = GCSLock.from_path(gcs_path_contention, owner_id=OWNER_ID_1, lease_sec=15)
    lock2_contention = GCSLock.from_path(gcs_path_contention, owner_id=OWNER_ID_2, lease_sec=15)

    await asyncio.gather(
        worker("Worker 1 (contention)", lock1_contention),
        worker("Worker 2 (contention)", lock2_contention),
    )

    logging.info("--- Contention test finished ---")
    await asyncio.sleep(2)

    # --- Part 2: Sequential Test ---
    logging.info("--- Running sequential test ---")
    logging.info("Two workers will now acquire the lock one after the other.")

    lock_name_sequential = f"locks/demo-lock-{uuid.uuid4()}.lock"
    gcs_path_sequential = f"gs://{gcs_bucket}/{lock_name_sequential}"
    logging.info(f"Using lock path for sequential test: {gcs_path_sequential}")

    lock1_sequential = GCSLock.from_path(gcs_path_sequential, owner_id=OWNER_ID_1, lease_sec=15)
    lock2_sequential = GCSLock.from_path(gcs_path_sequential, owner_id=OWNER_ID_2, lease_sec=15)

    await worker("Worker 1 (sequential)", lock1_sequential)
    await asyncio.sleep(1)
    await worker("Worker 2 (sequential)", lock2_sequential)

    logging.info("--- Sequential test finished ---")


if __name__ == "__main__":
    asyncio.run(main())
