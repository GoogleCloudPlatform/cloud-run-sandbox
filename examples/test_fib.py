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
import sys
import os

def fib(n):
    if n < 0:
        return -1
    elif n == 0:
        return 0
    elif n == 1 or n == 2:
        return 1
    else:
        return fib(n-1) + fib(n-2)

if __name__ == "__main__":
    i = 0
    sandbox_id = os.environ.get("CONTAINER_ID")
    prefix = f"[{sandbox_id}] " if sandbox_id else ""
    while True:
        output = f"{prefix}fib({i}) = {fib(i)}"
        print(output, flush=True)
        i += 1
        time.sleep(5)
