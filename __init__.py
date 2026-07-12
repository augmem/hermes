"""Standalone Cortext memory-provider plugin for Hermes Agent.

This repository must remain importable directly from Hermes's Git plugin
installer, without requiring a separately installed Python package.
"""

from .provider import CortextMemoryProvider, register

__all__ = ["CortextMemoryProvider", "register"]
