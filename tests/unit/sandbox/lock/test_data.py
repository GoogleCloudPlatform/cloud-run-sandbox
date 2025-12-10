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

import time
import pytest

from src.sandbox.lock.data import LockData


def test_lock_data_properties():
    """Tests basic property access."""
    raw_data = {
        "ownerId": "owner-1",
        "generation": 123,
        "waitingOwnerId": "waiter-1",
    }
    lock_data = LockData(raw=raw_data)
    assert lock_data.owner_id == "owner-1"
    assert lock_data.generation == 123
    assert lock_data.waiting_owner_id == "waiter-1"


def test_is_expired():
    """Tests the is_expired property."""
    expired_data = LockData(raw={"expiresAt": time.time() - 100})
    assert expired_data.is_expired is True

    active_data = LockData(raw={"expiresAt": time.time() + 100})
    assert active_data.is_expired is False
