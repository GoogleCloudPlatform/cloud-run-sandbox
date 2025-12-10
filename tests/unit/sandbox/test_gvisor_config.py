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
import json
import os
import shutil
import unittest
from unittest.mock import MagicMock, patch, AsyncMock, mock_open

import pytest
from src.sandbox.gvisor import GVisorConfig, GVisorSandbox

@pytest.fixture
def sandbox():
    """Returns a GVisorSandbox instance with a mock config."""
    s = GVisorSandbox(sandbox_id="test-sandbox", config=GVisorConfig())
    yield s
    # Cleanup
    if os.path.exists(s._bundle_dir):
        shutil.rmtree(s._bundle_dir)

@pytest.mark.asyncio
async def test_create_writable_filesystem(sandbox):
    """Tests that a writable filesystem is created when configured."""
    sandbox._config.writable_filesystem = True
    
    sandbox._prepare_bundle()
    
    config_path = os.path.join(sandbox._bundle_dir, "config.json")
    assert os.path.exists(config_path)
    
    with open(config_path, "r") as f:
        config = json.load(f)
        
    assert config["root"]["readonly"] is False

@pytest.mark.asyncio
async def test_create_readonly_filesystem(sandbox):
    """Tests that a readonly filesystem is created when configured."""
    sandbox._config.writable_filesystem = False

    sandbox._prepare_bundle()
    
    config_path = os.path.join(sandbox._bundle_dir, "config.json")
    assert os.path.exists(config_path)
    
    with open(config_path, "r") as f:
        config = json.load(f)
        
    assert config["root"]["readonly"] is True

def test_prepare_bundle_with_network_sandbox(sandbox):
    """
    Tests that _prepare_bundle correctly configures the OCI spec for a
    networked sandbox.
    """
    sandbox._config.network = "sandbox"
    sandbox._config.ip_address = "192.168.250.10"
    
    sandbox._prepare_bundle()

    # 1. Check that network config files were created
    bundle_dir = sandbox._bundle_dir
    resolv_path = os.path.join(bundle_dir, "resolv.conf")
    hostname_path = os.path.join(bundle_dir, "hostname")
    hosts_path = os.path.join(bundle_dir, "hosts")
    
    assert os.path.exists(resolv_path)
    assert os.path.exists(hostname_path)
    assert os.path.exists(hosts_path)

    with open(resolv_path, "r") as f:
        assert "nameserver 8.8.8.8" in f.read()
    with open(hostname_path, "r") as f:
        assert "test-sandbox" in f.read()
    with open(hosts_path, "r") as f:
        content = f.read()
        assert "127.0.0.1\tlocalhost" in content
        assert "192.168.250.10\ttest-sandbox" in content

    # 2. Check that config.json is correct
    config_path = os.path.join(bundle_dir, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)

    # Check for network namespace
    namespaces = config["linux"]["namespaces"]
    net_ns = next((ns for ns in namespaces if ns["type"] == "network"), None)
    assert net_ns is not None
    assert net_ns["path"] == "/var/run/netns/test-sandbox"

    # Check for mounts
    mounts = config["mounts"]
    assert any(m["destination"] == "/etc/resolv.conf" for m in mounts)
    assert any(m["destination"] == "/etc/hostname" for m in mounts)
    assert any(m["destination"] == "/etc/hosts" for m in mounts)

def test_build_runsc_cmd_defaults(sandbox):
    """Tests the default runsc command."""
    cmd = sandbox._build_runsc_cmd("run", "my-sandbox")
    assert "--network" in cmd
    assert "sandbox" in cmd
    assert "sudo" not in cmd
    assert "--rootless" not in cmd
    assert "--ignore-cgroups" not in cmd
    assert "--platform" in cmd
    assert "systrap" in cmd

def test_build_runsc_cmd_network_disabled(sandbox):
    """Tests that the network flag is correctly set when disabled."""
    sandbox._config.network = "none"
    cmd = sandbox._build_runsc_cmd("run", "my-sandbox")
    assert "--network" in cmd
    assert "none" in cmd

def test_build_runsc_cmd_no_network_flag(sandbox):
    """Tests that the network flag is not added to non-run commands."""
    cmd = sandbox._build_runsc_cmd("exec", "my-sandbox", "echo", "hello")
    assert "--network" not in cmd

def test_build_runsc_cmd_sudo(sandbox):
    """Tests that sudo is used when configured."""
    sandbox._config.use_sudo = True
    cmd = sandbox._build_runsc_cmd("run", "my-sandbox")
    assert cmd[0] == "sudo"

def test_build_runsc_cmd_rootless(sandbox):
    """Tests that the rootless flag is added when configured."""
    sandbox._config.rootless = True
    cmd = sandbox._build_runsc_cmd("run", "my-sandbox")
    assert "--rootless" in cmd

def test_build_runsc_cmd_ignore_cgroups(sandbox):
    """Tests that the ignore-cgroups flag is added when configured."""
    sandbox._config.ignore_cgroups = True
    cmd = sandbox._build_runsc_cmd("run", "my-sandbox")
    assert "--ignore-cgroups" in cmd

def test_build_runsc_cmd_platform(sandbox):
    """Tests that the platform flag is correctly set."""
    sandbox._config.platform = "kvm"
    cmd = sandbox._build_runsc_cmd("run", "my-sandbox")
    assert "--platform" in cmd
    assert "kvm" in cmd

def test_prepare_bundle_with_checkpointable_enabled(sandbox):
    """
    Tests that _prepare_bundle adds the cpufeatures annotation when checkpointable is True.
    """
    sandbox._config.checkpointable = True
    
    sandbox._prepare_bundle()
    
    config_path = os.path.join(sandbox._bundle_dir, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
        
    assert "annotations" in config
    assert "dev.gvisor.internal.cpufeatures" in config["annotations"]

def test_prepare_bundle_with_checkpointable_disabled(sandbox):
    """
    Tests that _prepare_bundle does not add the cpufeatures annotation when checkpointable is False.
    """
    sandbox._config.checkpointable = False
    
    sandbox._prepare_bundle()
    
    config_path = os.path.join(sandbox._bundle_dir, "config.json")
    with open(config_path, "r") as f:
        config = json.load(f)
        
    assert "dev.gvisor.internal.cpufeatures" not in config["annotations"]
