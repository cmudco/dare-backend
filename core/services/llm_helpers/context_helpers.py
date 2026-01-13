"""
Context Helpers Module

Pure functions for building and manipulating LLM message context.
These functions have no side effects and are easily testable.
"""

from typing import Dict, List


def build_transcription_context(transcriptions: Dict[str, str]) -> str:
    """
    Build formatted transcription context from video transcriptions.
    
    Args:
        transcriptions: Dict mapping video names to transcription text
        
    Returns:
        Formatted context string, or empty string if no successful transcriptions
    """
    successful_transcriptions = [
        f"Video '{video_name}' audio transcription:\n{transcription}"
        for video_name, transcription in transcriptions.items()
        if transcription
    ]
    
    if not successful_transcriptions:
        return ""
    
    return (
        "=== Video Audio Transcriptions ===\n\n"
        + "\n\n".join(successful_transcriptions)
        + "\n\n=== End of Video Transcriptions ===\n"
    )


def insert_context_before_last_user_message(
    messages: List[Dict], 
    context: str
) -> None:
    """
    Insert context message before the last user message in the list.
    
    Args:
        messages: List of message dicts (modified in place)
        context: Context string to insert
    """
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            messages.insert(i, {"role": "user", "content": context})
            break
