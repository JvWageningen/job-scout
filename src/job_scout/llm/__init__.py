"""LLM provider abstraction for job-scout."""

from job_scout.llm.base import CallPurpose, LLMClient, LLMError
from job_scout.llm.claude_cli import ClaudeCliClient
from job_scout.llm.factory import get_llm_client
from job_scout.llm.kilo_cli import KiloCliClient
from job_scout.llm.zai import ZaiClient

__all__ = [
    "CallPurpose",
    "ClaudeCliClient",
    "KiloCliClient",
    "LLMClient",
    "LLMError",
    "ZaiClient",
    "get_llm_client",
]
