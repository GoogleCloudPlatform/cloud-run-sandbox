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
from unittest.mock import patch

import pytest
from src.sandbox.factory import create_sandbox_instance

@pytest.fixture
def mock_gvisor_sandbox():
    """Mocks the GVisorSandbox class."""
    with patch("src.sandbox.factory.GVisorSandbox") as mock:
        yield mock

def test_create_sandbox_instance_defaults(mock_gvisor_sandbox):
    """Tests that the factory creates a sandbox with default settings."""
    with patch.dict(os.environ, {}, clear=True):
        create_sandbox_instance("test-sandbox")
        config = mock_gvisor_sandbox.call_args[1]["config"]

        assert config.use_sudo is False
        assert config.rootless is False
        assert config.ignore_cgroups is True
        assert config.platform == "systrap"
        assert config.network == "host"
        assert config.writable_filesystem is True

def test_create_sandbox_instance_with_env_vars(mock_gvisor_sandbox):
    """Tests that the factory correctly parses environment variables."""
    env_vars = {
        "RUNSC_USE_SUDO": "true",
        "RUNSC_ROOTLESS": "true",
        "RUNSC_IGNORE_CGROUPS": "false",
        "RUNSC_PLATFORM": "kvm",
        "RUNSC_DISABLE_NETWORKING": "true",
        "RUNSC_READONLY_FILESYSTEM": "true"
    }
    with patch.dict(os.environ, env_vars, clear=True):
        create_sandbox_instance("test-sandbox")
        config = mock_gvisor_sandbox.call_args[1]["config"]

        assert config.use_sudo is True
        assert config.rootless is True
        assert config.ignore_cgroups is False
        assert config.platform == "kvm"
        assert config.network == "none"
        assert config.writable_filesystem is False
