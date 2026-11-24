"""
OpenAI Whisper service for audio transcription from videos.

This service extracts audio from video files and transcribes it using OpenAI's Whisper API.
"""

import base64
import logging
import os
import subprocess
import tempfile
from typing import Optional, Dict

from openai import AsyncOpenAI

from config import env
from conversations.constants import Provider
from core.services.api_key_service import get_provider_api_key

logger = logging.getLogger(__name__)


class WhisperService:
    """Service for transcribing audio from videos using OpenAI Whisper."""

    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url=env.TRANSCRIPTION_BASE_URL)

    @classmethod
    async def for_platform(cls) -> "WhisperService":
        """The deployment's transcription endpoint (OpenAI unless configured)."""
        api_key = env.TRANSCRIPTION_API_KEY
        return cls(api_key or await get_provider_api_key(Provider.OPENAI.value))

    async def transcribe(
        self,
        file,
        model: Optional[str] = None,
        language: Optional[str] = None,
        response_format: str = "text",
        **options,
    ):
        params = {
            "model": model or env.TRANSCRIPTION_MODEL,
            "file": file,
            "response_format": response_format,
            **options,
        }
        if language:
            params["language"] = language
        return await self.client.audio.transcriptions.create(**params)

    async def transcribe_video(self, video_data: Dict, language: Optional[str] = None) -> Optional[str]:
        """
        Transcribe audio from a video file.

        Args:
            video_data: Dictionary with 'preview' (base64 data URL) and 'name'
            language: Optional language code (e.g., 'en', 'es')

        Returns:
            Transcribed text or None if transcription fails
        """
        try:
            # Extract base64 data from data URL
            preview = video_data.get('preview', '')
            if ',' in preview:
                base64_data = preview.split(',')[1]
            else:
                base64_data = preview

            # Decode base64 to bytes
            video_bytes = base64.b64decode(base64_data)

            # Extract audio from video
            audio_bytes = self._extract_audio(video_bytes)
            if not audio_bytes:
                logger.error("Failed to extract audio from video")
                return None

            # Create temporary file for Whisper API
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as audio_file:
                audio_file.write(audio_bytes)
                audio_file_path = audio_file.name

            try:
                # Call Whisper API
                with open(audio_file_path, 'rb') as audio_file:
                    transcript = await self.transcribe(audio_file, language=language)

                logger.info(f"Successfully transcribed video audio")
                return transcript

            finally:
                # Clean up temp file
                if os.path.exists(audio_file_path):
                    os.unlink(audio_file_path)

        except Exception as e:
            logger.error(f"Error transcribing video audio: {str(e)}")
            return None

    def _extract_audio(self, video_bytes: bytes) -> Optional[bytes]:
        """
        Extract audio from video file.

        Args:
            video_bytes: Video file bytes

        Returns:
            Audio file bytes (MP3) or None if extraction fails
        """
        try:
            # Create temporary video file
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as video_file:
                video_file.write(video_bytes)
                video_file_path = video_file.name

            # Create temporary audio file path
            audio_file_path = video_file_path.replace('.mp4', '.mp3')

            try:
                # Use ffmpeg to extract audio
                # Check if ffmpeg is available
                try:
                    subprocess.run(
                        ['ffmpeg', '-version'],
                        capture_output=True,
                        check=True
                    )
                except (subprocess.CalledProcessError, FileNotFoundError):
                    logger.error("ffmpeg not found - cannot extract audio from video")
                    return None

                # Extract audio using ffmpeg
                subprocess.run(
                    [
                        'ffmpeg',
                        '-i', video_file_path,
                        '-vn',  # No video
                        '-acodec', 'libmp3lame',  # MP3 codec
                        '-ar', '16000',  # 16kHz sample rate (optimal for Whisper)
                        '-ac', '1',  # Mono
                        '-b:a', '64k',  # 64kbps bitrate
                        '-y',  # Overwrite output
                        audio_file_path
                    ],
                    capture_output=True,
                    check=True
                )

                # Read extracted audio
                with open(audio_file_path, 'rb') as audio_file:
                    audio_bytes = audio_file.read()

                return audio_bytes

            finally:
                # Clean up temp files
                if os.path.exists(video_file_path):
                    os.unlink(video_file_path)
                if os.path.exists(audio_file_path):
                    os.unlink(audio_file_path)

        except Exception as e:
            logger.error(f"Error extracting audio from video: {str(e)}")
            return None

    async def transcribe_multiple_videos(
        self,
        videos: list[Dict],
        language: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """
        Transcribe audio from multiple videos.

        Args:
            videos: List of video dictionaries with 'preview' and 'name'
            language: Optional language code

        Returns:
            Dictionary mapping video names to transcriptions
        """
        transcriptions = {}

        for video in videos:
            video_name = video.get('name', 'unknown')
            transcription = await self.transcribe_video(video, language)
            transcriptions[video_name] = transcription

        return transcriptions
