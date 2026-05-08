"""
Response Builders Module

Pure data transformation functions for building response dictionaries.
These functions have no side effects, no DB access, and are easily testable.
"""

from typing import Dict, Optional


def build_transcription_data(usage: Dict) -> Optional[Dict]:
    """
    Build transcription response data from usage dict.

    Args:
        usage: Usage dict containing transcription_result

    Returns:
        Dict with transcription data for frontend, or None
    """
    transcription = usage.get("transcription_result")
    if not transcription:
        return None

    return {
        "fileId": transcription.get("file_id"),
        "fileName": transcription.get("file_name"),
        "text": transcription.get("text"),
        "language": transcription.get("language", "auto"),
        "model": transcription.get("model", "whisper-1"),
        "cost": str(usage.get("cost")) if usage.get("cost") else None,
        "duration": transcription.get("duration"),
        "transcribedAt": transcription.get("transcribed_at"),
    }


def build_usage_with_totals(usage: Optional[Dict]) -> Optional[Dict]:
    """
    Build usage dict with total_tokens calculated.

    Args:
        usage: Raw usage dict from LLM

    Returns:
        Usage dict with total_tokens added, or None
    """
    if not usage or not isinstance(usage, dict):
        return usage

    inp = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    out = usage.get("output_tokens") or usage.get("completion_tokens") or 0

    result = dict(usage)
    result["total_tokens"] = (inp or 0) + (out or 0)
    return result
