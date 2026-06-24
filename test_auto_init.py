#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test auto-init logic"""

import sys
import os
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

import campus_auto_login

# Test 1: Check version
print(f"Version: {campus_auto_login.__version__}")
assert campus_auto_login.__version__ == "1.1.0", "Version should be 1.1.0"
print("[PASS] Version check passed")

# Test 2: Check _find_config_file returns None when no config exists
fake_config = Path("nonexistent_config.json")
result = campus_auto_login._find_config_file(fake_config)
assert result is None, "_find_config_file should return None for nonexistent file"
print("[PASS] _find_config_file returns None for nonexistent file")

# Test 3: Check _find_config_file finds existing config
if campus_auto_login.DEFAULT_CONFIG.exists():
    result = campus_auto_login._find_config_file(campus_auto_login.DEFAULT_CONFIG)
    assert result is not None, "_find_config_file should find existing config"
    print(f"[PASS] _find_config_file found existing config: {result}")
else:
    print("[SKIP] No existing config file, skipping find test")

# Test 4: Verify parse_args works
# Note: parse_args() reads from sys.argv, so we can't easily test it without modifying sys.argv
# Just verify it's callable
try:
    # Save original argv
    original_argv = sys.argv[:]
    sys.argv = ["campus_auto_login.py"]
    args = campus_auto_login.parse_args()
    assert not args.init, "init should be False by default"
    assert not args.tray, "tray should be False by default"
    print("[PASS] parse_args default values correct")
finally:
    # Restore original argv
    sys.argv = original_argv

print("\n" + "="*60)
print("All auto-init logic tests passed!")
print("="*60)
