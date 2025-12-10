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
Data class for representing and interpreting the state of a distributed lock.
"""
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class LockData:
    """
    Wraps the raw lock data dictionary and provides helper properties for easy state
    checking.
    """

    raw: Dict[str, Any]

    @property
    def owner_id(self) -> Optional[str]:
        return self.raw.get("ownerId")

    @property
    def generation(self) -> Optional[int]:
        return self.raw.get("generation")

    @property
    def is_expired(self) -> bool:
        """Checks if the lock's main lease has expired."""
        return self.raw.get("expiresAt", 0) < time.time()

    @property
    def waiting_owner_id(self) -> Optional[str]:
        return self.raw.get("waitingOwnerId")

    @property
    def has_waiter(self) -> bool:
        """Checks if a waiter is registered, regardless of expiration."""
        return self.raw.get("waitingOwnerId") is not None

    @property
    def is_waiter_expired(self) -> bool:
        """Checks if the registered waiter's lease has expired."""
        return self.raw.get("waiterExpiresAt", 0) < time.time()
