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

from fastapi import FastAPI
from .handlers import http, websocket
from .sandbox.manager import SandboxManager
from .sandbox.lock.factory import LockFactory
from .sandbox.config import GCSConfig
from contextlib import asynccontextmanager
import logging

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles application startup and shutdown events.
    """
    # --- Startup ---
    # 1. Load GCS config from environment
    gcs_config = GCSConfig.from_env()
    lock_factory = None
    
    # 2. If a metadata bucket is configured, create a real GCS lock factory
    if gcs_config.metadata_bucket:
        logging.info(f"GCS persistence enabled (bucket: {gcs_config.metadata_bucket}).")
        lock_factory = LockFactory(gcs_bucket_name=gcs_config.metadata_bucket)
    else:
        logging.info("GCS persistence not configured. Using in-memory lock factory.")
        lock_factory = LockFactory()

    # 3. Create and configure the sandbox manager and enable idle cleanup
    manager = SandboxManager(gcs_config=gcs_config, lock_factory=lock_factory)
    manager.enable_idle_cleanup()

    # 4. Inject the configured manager into the handler modules
    http.manager = manager
    websocket.manager = manager
    
    yield
    
    # --- Shutdown ---
    await manager.delete_all_sandboxes()

app = FastAPI(lifespan=lifespan)

app.include_router(http.router)
app.include_router(websocket.router)

@app.get("/")
def read_root():
    return {"message": "Server is running"}
