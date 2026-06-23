# tests/conftest.py
"""
Pytest configuration and shared fixtures for Phase 0 tests.
"""
import sys
import os
from pathlib import Path

# Add project root to path so all imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set config path for tests
os.environ.setdefault("CONFIG_PATH", str(Path(__file__).parent.parent / "config" / "config.yaml"))
