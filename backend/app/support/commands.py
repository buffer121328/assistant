"""Compatibility exports for HTTP adapters that parse user command tokens."""

from application.command_catalog import parse_task_type
from features import FEATURE_COMMANDS

__all__ = ["FEATURE_COMMANDS", "parse_task_type"]
