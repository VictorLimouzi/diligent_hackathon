"""LLM analysis and agent features for Casebook (Anthropic API).

Advisory only: nothing in this package produces or alters an engine decision.
Every function works with no API key, by replaying the on-disk response cache
or by a deterministic mock, and says so (`cached: true` or `stub: true`).

The server calls exactly these five (see contracts/python_interfaces.md).
"""
from .chat import chat
from .client import llm_status
from .investigator import investigate
from .note import write_note
from .queue import queue_summary

__all__ = ["llm_status", "write_note", "investigate", "chat", "queue_summary"]
