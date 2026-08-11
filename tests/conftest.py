# -*- coding: utf-8 -*-
"""tests/conftest.py - pytest configuration for office-token-booster tests"""

def pytest_configure(config):
    for marker in ("smoke", "integration", "blackbox", "whitebox",
              "metadata", "contract", "privacy", "portability",
              "golden", "regression"):
        config.addinivalue_line("markers", f"{marker}: mark test as a {marker} test")
