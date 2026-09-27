"""Audit-test package marker.

These tests assert structural/contract invariants (AST scans, baselines) rather
than runtime behaviour. Shared fixtures come from the repository-level
``conftest.py`` and ``tests/conftest.py``; nothing audit-specific is required
here beyond keeping the directory a collected test package.
"""
