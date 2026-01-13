"""
Media Helpers Module

Async functions for processing media files (video transcription, audio transcription, etc.)
for LLM context enrichment.
"""

import logging
from datetime import datetime
from typing import List, Dict, Optional, Tuple, AsyncGenerator

from conversations.constants import Provider
from conversations.services.audio_transcription_service import AudioTranscriptionService
from core.services.api_key_service import get_provider_api_key, get_provider_api_key_for_user
from core.services.whisper_service import WhisperService
from .context_helpers import build_transcription_context, insert_context_before_last_user_message
from .db_helpers import get_audio_or_video_files


logger = logging.getLogger(__name__)


async def add_video_transcriptions_to_messages(
    media_items: List[Dict],
    messages: List[Dict],
    user=None
) -> List[Dict]:
    """
    Add video transcriptions to message context for LLMs.

    Extracts and transcribes audio from videos, then adds the transcriptions
    to the message context so LLMs have access to the spoken content.

    Args:
        media_items: List of media dicts with 'preview', 'type', 'name'
        messages: List of message dictionaries
        user: Optional user for API key resolution

    Returns:
        Updated messages list with video transcriptions added
    """
    videos = [item for item in media_items if item.get('type', '').startswith('video/')]

    if not videos:
        return messages

    try:
        api_key = await _get_openai_api_key(user)
        whisper_service = WhisperService(api_key=api_key)

        transcriptions = await whisper_service.transcribe_multiple_videos(videos)

        context = build_transcription_context(transcriptions)
        if context:
            insert_context_before_last_user_message(messages, context)
            successful_count = len([t for t in transcriptions.values() if t])
            logger.info(f"Added transcriptions for {successful_count} video(s)")

    except Exception as e:
        logger.error(f"Error transcribing videos: {str(e)}")
        # Don't fail the entire request if transcription fails

    return messages


async def _get_openai_api_key(user=None) -> str:
    """Get OpenAI API key based on user context."""
    if user:
        return await get_provider_api_key_for_user(Provider.OPENAI.value, user)
    return await get_provider_api_key(Provider.OPENAI.value)


async def execute_audio_transcription(
    media_ids: List[int],
    llm_identifier: str,
    audio_transcription_settings: Optional[Dict] = None,
) -> AsyncGenerator[Tuple[str, Optional[Dict]], None]:
    """
    Execute audio transcription request with streaming support.

    For large files that get split into chunks, this yields each chunk's
    transcription as it completes, allowing real-time progress updates.

    Args:
        media_ids: List of media file IDs to transcribe
        llm_identifier: Model identifier (e.g., 'whisper-1')
        audio_transcription_settings: Optional settings dict with 'language', 'stream_chunks'

    Yields:
        Tuple of (chunk: str, usage: Dict) where usage contains:
        - For intermediate chunks: None
        - For final chunk: {"transcription_result": {...}}
    """
    media_files = await get_audio_or_video_files(media_ids)

    if not media_files:
        yield "Error: No audio or video files found. Please upload an audio/video file to transcribe.", None
        return

    settings = audio_transcription_settings or {}
    language = settings.get("language", "auto")
    language = None if language == "auto" else language
    enable_streaming = settings.get("stream_chunks", True)

    accumulated_text = ""
    final_transcription = None

    for media_file in media_files:
        try:
            if enable_streaming:
                async for chunk_result in _transcribe_file_streaming(
                    media_file, language, llm_identifier
                ):
                    if chunk_result.get("error"):
                        yield chunk_result["error_message"], None
                        return

                    accumulated_text = chunk_result["accumulated_text"]
                    yield accumulated_text, None

                    if chunk_result.get("final_transcription"):
                        final_transcription = chunk_result["final_transcription"]
            else:
                final_transcription = await AudioTranscriptionService.transcribe_audio_file(
                    file_obj=media_file,
                    language=language,
                    model=llm_identifier
                )

        except Exception as e:
            logger.exception(f"Error transcribing media file {media_file.id}: {str(e)}")
            yield f"Error transcribing {media_file.name}: {str(e)}", None
            return

    if final_transcription:
        result_text = AudioTranscriptionService.format_transcription_for_display(final_transcription)
        usage_data = final_transcription.copy()
        usage_data["transcription_result"] = final_transcription
        yield result_text, usage_data
    else:
        file_names = ", ".join([f.name for f in media_files]) if media_files else "unknown"
        yield f"Error: Transcription failed for {file_names}. Please check the server logs for more details.", None


async def _transcribe_file_streaming(
    media_file,
    language: Optional[str],
    model: str,
) -> AsyncGenerator[Dict, None]:
    """
    Stream transcription chunks for a single file.

    Yields dicts with:
    - accumulated_text: Progressive transcription text
    - error: True if error occurred
    - error_message: Error details (if error)
    - final_transcription: Complete transcription dict (on last chunk)
    """
    file_name = media_file.name
    chunk_texts = []
    accumulated_text = ""

    async for chunk_data in AudioTranscriptionService.transcribe_audio_file_streaming(
        file_obj=media_file,
        language=language,
        model=model
    ):
        if chunk_data.get("error"):
            error_msg = chunk_data.get("error_message", "Unknown transcription error")
            logger.error(f"Transcription error for {file_name}: {error_msg}")
            yield {"error": True, "error_message": f"Error transcribing {file_name}: {error_msg}"}
            return

        chunk_text = chunk_data["text"]
        chunk_texts.append(chunk_text)

        if chunk_data["chunk_index"] == 0:
            accumulated_text = f"**Transcription of `{file_name}`**\n\n{chunk_text}"
        else:
            accumulated_text += " " + chunk_text

        yield {"accumulated_text": accumulated_text}

    if chunk_texts:
        yield {
            "accumulated_text": accumulated_text,
            "final_transcription": {
                'text': " ".join(chunk_texts),
                'language': language or 'auto',
                'model': model,
                'file_id': media_file.id,
                'file_name': media_file.name,
                'file_size': media_file.size,
                'media_type': media_file.media_type,
                'transcribed_at': datetime.now().isoformat(),
            }
        }
