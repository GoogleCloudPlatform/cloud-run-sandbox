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

import os
from .gvisor import GVisorSandbox, GVisorConfig

def make_sandbox_config() -> GVisorConfig:
    """Creates a GVisorConfig from environment variables."""
    return GVisorConfig(
        use_sudo=os.environ.get('RUNSC_USE_SUDO', 'false').lower() in ['true', '1'],
        rootless=os.environ.get('RUNSC_ROOTLESS', 'false').lower() in ['true', '1'],
        root_dir_base=os.environ.get('RUNSC_ROOT_DIR_BASE', '/tmp'),
        bundle_dir_base=os.environ.get('RUNSC_BUNDLE_DIR_BASE', '/tmp'),
        ignore_cgroups=os.environ.get('RUNSC_IGNORE_CGROUPS', 'true').lower() in ['true', '1'],
        platform=os.environ.get('RUNSC_PLATFORM', 'systrap'),
        network='none' if os.environ.get('RUNSC_DISABLE_NETWORKING', 'false').lower() in ['true', '1'] else 'host',
        writable_filesystem=os.environ.get('RUNSC_READONLY_FILESYSTEM', 'false').lower() not in ['true', '1'],
        debug=os.environ.get('GVISOR_DEBUG', 'false').lower() in ['true', '1'],
        strace=os.environ.get('GVISOR_STRACE', 'false').lower() in ['true', '1'],
        log_packets=os.environ.get('GVISOR_LOG_PACKETS', 'false').lower() in ['true', '1'],
        debug_log_dir=os.environ.get('GVISOR_DEBUG_LOG_DIR', '/tmp/runsc')
    )

def create_sandbox_instance(sandbox_id: str, config: GVisorConfig = None):
    """
    Factory function to create a sandbox instance based on configuration.
    If a config is not provided, it will be created from environment variables.
    """
    if config is None:
        config = make_sandbox_config()
    return GVisorSandbox(sandbox_id, config=config)