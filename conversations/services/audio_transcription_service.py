"""
Audio Transcription Service

Handles audio file transcription using Whisper API or other transcription services.
Saves transcriptions as structured data that can be displayed in conversations.
"""

import logging
from typing import Dict, Optional, List
from datetime import datetime
from files.models import File
from core.services.whisper_service import WhisperService
from core.services.api_key_service import get_provider_api_key
from conversations.constants import Provider

logger = logging.getLogger(__name__)


class AudioTranscriptionService:
    """Service for handling audio file transcription."""

    @staticmethod
    async def transcribe_audio_file(
        file_obj: File,
        language: Optional[str] = None,
        model: str = "whisper-1"
    ) -> Optional[Dict]:
        """
        Transcribe an audio file using Whisper API.

        Args:
            file_obj: File object containing the audio file
            language: Optional language code (e.g., 'en', 'es', 'fr')
            model: Transcription model to use (default: 'whisper-1')

        Returns:
            Dictionary containing transcription result with:
            - text: The transcribed text
            - language: Detected or specified language
            - duration: Audio duration (if available)
            - model: Model used for transcription
            - file_id: ID of the transcribed file
            - file_name: Name of the transcribed file
        """
        try:
            logger.info(f"Starting transcription for file ID: {file_obj.id} ({file_obj.name})")

            # Get the audio file path
            audio_file_path = file_obj.file.path

            # Get API key asynchronously (we're in async context)
            api_key = await get_provider_api_key(Provider.OPENAI.value)

            # Use WhisperService to transcribe
            whisper_service = WhisperService(api_key=api_key)

            # Directly call Whisper API with the file
            with open(audio_file_path, 'rb') as audio_file:
                transcript_params = {
                    "model": model,
                    "file": audio_file,
                    "response_format": "text"
                }

                if language:
                    transcript_params["language"] = language

                transcription_text = await whisper_service.client.audio.transcriptions.create(**transcript_params)

            if not transcription_text:
                logger.error(f"Transcription failed for file ID: {file_obj.id}")
                return None

            # Prepare transcription result
            result = {
                'text': transcription_text,
                'language': language or 'auto',
                'model': model,
                'file_id': file_obj.id,
                'file_name': file_obj.name,
                'file_size': file_obj.size,
                'media_type': file_obj.media_type,
                'transcribed_at': datetime.now().isoformat(),
            }

            logger.info(f"Successfully transcribed file ID: {file_obj.id} - {len(transcription_text)} characters")
            return result

        except Exception as e:
            logger.exception(f"Error transcribing audio file ID {file_obj.id}: {str(e)}")
            return None

    @staticmethod
    async def transcribe_multiple_audio_files(
        file_objects: List[File],
        language: Optional[str] = None,
        model: str = "whisper-1"
    ) -> List[Dict]:
        """
        Transcribe multiple audio files.

        Args:
            file_objects: List of File objects to transcribe
            language: Optional language code
            model: Transcription model to use

        Returns:
            List of transcription result dictionaries
        """
        results = []
        for file_obj in file_objects:
            result = await AudioTranscriptionService.transcribe_audio_file(
                file_obj=file_obj,
                language=language,
                model=model
            )
            if result:
                results.append(result)
        return results

    @staticmethod
    def format_transcription_for_display(transcription: Dict) -> str:
        """
        Format transcription result for display in conversation.

        Args:
            transcription: Transcription result dictionary

        Returns:
            Formatted string for display
        """
        file_name = transcription.get('file_name', 'Unknown')
        language = transcription.get('language', 'auto')
        text = transcription.get('text', '')

        formatted = f"**Transcription of `{file_name}`**\n\n"
        if language and language != 'auto':
            formatted += f"*Language: {language}*\n\n"
        formatted += f"{text}"

        return formatted
