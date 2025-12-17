"""
Audio Transcription Service

Handles audio file transcription using Whisper API or other transcription services.
Saves transcriptions as structured data that can be displayed in conversations.
"""

import logging
import base64
import tempfile
import subprocess
import os
from typing import Dict, Optional, List, AsyncGenerator, Callable, Awaitable
from datetime import datetime
from files.models import File
from core.services.whisper_service import WhisperService
from core.services.api_key_service import get_provider_api_key
from conversations.constants import Provider

logger = logging.getLogger(__name__)

# Whisper API file size limit (25MB)
WHISPER_MAX_FILE_SIZE = 25 * 1024 * 1024  # 26214400 bytes

# Diarization model identifier
DIARIZE_MODEL = "gpt-4o-transcribe-diarize"

# Diarization model max duration (1400 seconds, use 1300 to be safe)
DIARIZE_MAX_DURATION_SECONDS = 1300


class AudioTranscriptionService:
    """Service for handling audio file transcription."""

    @staticmethod
    def _get_audio_duration(audio_file_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        duration_cmd = [
            'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', audio_file_path
        ]
        duration_output = subprocess.run(duration_cmd, capture_output=True, text=True)
        return float(duration_output.stdout.strip())

    @staticmethod
    def split_audio_by_duration(audio_file_path: str, max_duration: int = DIARIZE_MAX_DURATION_SECONDS) -> List[str]:
        """
        Split audio file into chunks based on duration.

        Args:
            audio_file_path: Path to the audio file
            max_duration: Maximum duration per chunk in seconds

        Returns:
            List of paths to chunked audio files
        """
        total_duration = AudioTranscriptionService._get_audio_duration(audio_file_path)

        if total_duration <= max_duration:
            return [audio_file_path]

        logger.info(f"Audio duration ({total_duration}s) exceeds diarization limit ({max_duration}s). Splitting into chunks...")

        # Split audio into chunks
        chunk_files = []
        temp_dir = tempfile.mkdtemp()
        chunk_index = 0
        start_time = 0

        while start_time < total_duration:
            chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index}.mp3")

            split_cmd = [
                'ffmpeg', '-i', audio_file_path,
                '-ss', str(start_time),
                '-t', str(max_duration),
                '-acodec', 'libmp3lame',
                '-ar', '16000',  # 16kHz
                '-ac', '1',      # Mono
                '-b:a', '64k',   # 64kbps
                '-y',
                chunk_path
            ]

            subprocess.run(split_cmd, capture_output=True, check=True)
            chunk_files.append(chunk_path)

            start_time += max_duration
            chunk_index += 1

        logger.info(f"Split audio into {len(chunk_files)} chunks by duration")
        return chunk_files

    @staticmethod
    def split_audio_file(audio_file_path: str, max_size: int = WHISPER_MAX_FILE_SIZE) -> List[str]:
        """
        Split large audio file into smaller chunks using ffmpeg.

        Args:
            audio_file_path: Path to the audio file
            max_size: Maximum size per chunk in bytes

        Returns:
            List of paths to chunked audio files
        """
        file_size = os.path.getsize(audio_file_path)

        if file_size <= max_size:
            return [audio_file_path]

        logger.info(f"Audio file size ({file_size} bytes) exceeds Whisper limit ({max_size} bytes). Splitting into chunks...")

        # Get audio duration
        total_duration = AudioTranscriptionService._get_audio_duration(audio_file_path)

        # Calculate chunk duration to stay under max_size
        # Rough estimate: size_per_second = file_size / duration
        size_per_second = file_size / total_duration
        chunk_duration = int((max_size * 0.95) / size_per_second)  # 95% of max to be safe

        logger.info(f"Splitting {total_duration}s audio into ~{chunk_duration}s chunks")

        # Split audio into chunks
        chunk_files = []
        temp_dir = tempfile.mkdtemp()
        chunk_index = 0
        start_time = 0

        while start_time < total_duration:
            chunk_path = os.path.join(temp_dir, f"chunk_{chunk_index}.mp3")

            split_cmd = [
                'ffmpeg', '-i', audio_file_path,
                '-ss', str(start_time),
                '-t', str(chunk_duration),
                '-acodec', 'libmp3lame',
                '-ar', '16000',  # 16kHz for Whisper
                '-ac', '1',      # Mono
                '-b:a', '64k',   # 64kbps
                '-y',
                chunk_path
            ]

            subprocess.run(split_cmd, capture_output=True, check=True)
            chunk_files.append(chunk_path)

            start_time += chunk_duration
            chunk_index += 1

        logger.info(f"Split audio into {len(chunk_files)} chunks")
        return chunk_files

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
            logger.info(f"Starting transcription for file ID: {file_obj.id} ({file_obj.name}, type: {file_obj.media_type})")

            # Get API key asynchronously (we're in async context)
            api_key = await get_provider_api_key(Provider.OPENAI.value)

            # Use WhisperService to transcribe
            whisper_service = WhisperService(api_key=api_key)

            # Handle video files differently (need to extract audio first)
            if file_obj.media_type == 'video':
                logger.info(f"Processing video file - will extract audio first")
                # For videos, we need to read the file and create the video_data dict
                file_path = file_obj.file.path
                with open(file_path, 'rb') as f:
                    file_bytes = f.read()
                    base64_data = base64.b64encode(file_bytes).decode('utf-8')
                    video_data = {
                        'preview': f'data:video/mp4;base64,{base64_data}',
                        'name': file_obj.name
                    }

                transcription_text = await whisper_service.transcribe_video(video_data, language)
            else:
                # For audio files, check size/duration and split if needed
                audio_file_path = file_obj.file.path

                # Use duration-based splitting for diarization model, size-based for Whisper
                if model == DIARIZE_MODEL:
                    chunk_files = AudioTranscriptionService.split_audio_by_duration(audio_file_path)
                else:
                    chunk_files = AudioTranscriptionService.split_audio_file(audio_file_path)

                transcription_texts = []
                temp_files_to_cleanup = []

                try:
                    for idx, chunk_path in enumerate(chunk_files):
                        if chunk_path != audio_file_path:
                            temp_files_to_cleanup.append(chunk_path)

                        logger.info(f"Transcribing chunk {idx + 1}/{len(chunk_files)}")

                        with open(chunk_path, 'rb') as audio_file:
                            # OpenAI needs the filename with correct extension
                            # For chunks, use .mp3 extension (what we converted to)
                            # For original file, use the original extension
                            if chunk_path == audio_file_path:
                                # Original file - use original name
                                chunk_name = file_obj.name
                            else:
                                # Chunk file - use .mp3 extension
                                base_name = os.path.splitext(file_obj.name)[0]
                                chunk_name = f"{base_name}_chunk{idx}.mp3"

                            # Use diarized_json format for diarization model
                            response_format = "diarized_json" if model == DIARIZE_MODEL else "text"
                            
                            transcript_params = {
                                "model": model,
                                "file": (chunk_name, audio_file),
                                "response_format": response_format
                            }
                            
                            # Add chunking_strategy for diarization model (required for audio > 30s)
                            if model == DIARIZE_MODEL:
                                transcript_params["chunking_strategy"] = "auto"

                            if language:
                                transcript_params["language"] = language

                            result = await whisper_service.client.audio.transcriptions.create(**transcript_params)
                            
                            # Parse diarized response if using diarize model
                            if model == DIARIZE_MODEL and response_format == "diarized_json":
                                chunk_text = AudioTranscriptionService._format_diarized_response(result)
                            else:
                                chunk_text = result
                            
                            transcription_texts.append(chunk_text)

                    # Combine all chunks
                    transcription_text = " ".join(transcription_texts)

                finally:
                    # Cleanup temporary chunk files
                    for temp_file in temp_files_to_cleanup:
                        try:
                            if os.path.exists(temp_file):
                                os.unlink(temp_file)
                            # Remove temp directory if it's empty
                            temp_dir = os.path.dirname(temp_file)
                            if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                                os.rmdir(temp_dir)
                        except Exception as cleanup_error:
                            logger.warning(f"Failed to cleanup temp file {temp_file}: {cleanup_error}")

            if not transcription_text:
                error_msg = f"Transcription returned empty result for file: {file_obj.name}"
                logger.error(f"Transcription failed for file ID: {file_obj.id}")
                raise Exception(error_msg)

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
            raise

    @staticmethod
    async def transcribe_audio_file_streaming(
        file_obj: File,
        language: Optional[str] = None,
        model: str = "whisper-1"
    ) -> AsyncGenerator[Dict, None]:
        """
        Transcribe an audio file using Whisper API, yielding each chunk as it completes.

        For large files that get split into chunks, this method yields each chunk's
        transcription as soon as it's ready, allowing real-time progress updates.

        Args:
            file_obj: File object containing the audio file
            language: Optional language code (e.g., 'en', 'es', 'fr')
            model: Transcription model to use (default: 'whisper-1')

        Yields:
            Dictionary with chunk data:
            {"chunk_index": int, "total_chunks": int, "text": str, "is_final": bool,
             "file_id": str, "file_name": str, "full_result": Optional[Dict]}
            The final yield includes "full_result" with the complete transcription data.
        """
        try:
            logger.info(f"Starting streaming transcription for file ID: {file_obj.id} ({file_obj.name})")

            api_key = await get_provider_api_key(Provider.OPENAI.value)
            whisper_service = WhisperService(api_key=api_key)

            transcription_text = ""

            # Handle video files - extract audio first (no streaming for video extraction)
            if file_obj.media_type == 'video':
                logger.info(f"Processing video file - will extract audio first")
                file_path = file_obj.file.path
                with open(file_path, 'rb') as f:
                    file_bytes = f.read()
                    base64_data = base64.b64encode(file_bytes).decode('utf-8')
                    video_data = {
                        'preview': f'data:video/mp4;base64,{base64_data}',
                        'name': file_obj.name
                    }

                transcription_text = await whisper_service.transcribe_video(video_data, language)

                # Yield single chunk for video (no splitting)
                yield {
                    "chunk_index": 0,
                    "total_chunks": 1,
                    "text": transcription_text,
                    "is_final": True,
                    "file_id": file_obj.id,
                    "file_name": file_obj.name,
                }
            else:
                # For audio files, split and stream each chunk
                audio_file_path = file_obj.file.path
                
                # Use duration-based splitting for diarization model, size-based for Whisper
                if model == DIARIZE_MODEL:
                    chunk_files = AudioTranscriptionService.split_audio_by_duration(audio_file_path)
                else:
                    chunk_files = AudioTranscriptionService.split_audio_file(audio_file_path)
                    
                total_chunks = len(chunk_files)

                transcription_texts = []
                temp_files_to_cleanup = []

                try:
                    for idx, chunk_path in enumerate(chunk_files):
                        if chunk_path != audio_file_path:
                            temp_files_to_cleanup.append(chunk_path)

                        logger.info(f"Transcribing chunk {idx + 1}/{total_chunks}")

                        with open(chunk_path, 'rb') as audio_file:
                            if chunk_path == audio_file_path:
                                chunk_name = file_obj.name
                            else:
                                base_name = os.path.splitext(file_obj.name)[0]
                                chunk_name = f"{base_name}_chunk{idx}.mp3"

                            # Use diarized_json format for diarization model
                            response_format = "diarized_json" if model == DIARIZE_MODEL else "text"
                            
                            transcript_params = {
                                "model": model,
                                "file": (chunk_name, audio_file),
                                "response_format": response_format
                            }
                            
                            # Add chunking_strategy for diarization model (required for audio > 30s)
                            if model == DIARIZE_MODEL:
                                transcript_params["chunking_strategy"] = "auto"

                            if language:
                                transcript_params["language"] = language

                            result = await whisper_service.client.audio.transcriptions.create(**transcript_params)
                            
                            # Parse diarized response if using diarize model
                            if model == DIARIZE_MODEL and response_format == "diarized_json":
                                chunk_text = AudioTranscriptionService._format_diarized_response(result)
                            else:
                                chunk_text = result
                            
                            transcription_texts.append(chunk_text)

                            # Yield chunk transcription immediately
                            is_final = (idx == total_chunks - 1)
                            yield {
                                "chunk_index": idx,
                                "total_chunks": total_chunks,
                                "text": chunk_text,
                                "is_final": is_final,
                                "file_id": file_obj.id,
                                "file_name": file_obj.name,
                            }

                finally:
                    # Cleanup temporary chunk files
                    for temp_file in temp_files_to_cleanup:
                        try:
                            if os.path.exists(temp_file):
                                os.unlink(temp_file)
                            temp_dir = os.path.dirname(temp_file)
                            if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                                os.rmdir(temp_dir)
                        except Exception as cleanup_error:
                            logger.warning(f"Failed to cleanup temp file {temp_file}: {cleanup_error}")

                transcription_text = " ".join(transcription_texts)

            if not transcription_text:
                error_msg = f"Transcription returned empty result for file: {file_obj.name}"
                logger.error(f"Streaming transcription failed for file ID: {file_obj.id}")
                yield {
                    "error": True,
                    "error_message": error_msg,
                    "file_id": file_obj.id,
                    "file_name": file_obj.name,
                }
                return

            logger.info(f"Successfully completed streaming transcription for file ID: {file_obj.id}")

        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Error in streaming transcription for file ID {file_obj.id}: {error_msg}")
            yield {
                "error": True,
                "error_message": error_msg,
                "file_id": file_obj.id,
                "file_name": file_obj.name,
            }
            return

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
    def _format_diarized_response(response) -> str:
        """
        Format diarized JSON response into readable text with speaker labels.
        
        Args:
            response: Diarized response from OpenAI API
            
        Returns:
            Formatted text with speaker labels
        """
        try:
            # Handle response object or dict
            if hasattr(response, 'model_dump'):
                data = response.model_dump()
            elif hasattr(response, 'to_dict'):
                data = response.to_dict()
            elif isinstance(response, dict):
                data = response
            else:
                # If response is just text, return as-is
                return str(response)
            
            # Extract segments with speaker labels
            segments = data.get('segments', [])
            if not segments:
                # Fallback to text field if no segments
                return data.get('text', str(response))
            
            formatted_lines = []
            current_speaker = None
            current_text = []
            
            for segment in segments:
                speaker = segment.get('speaker', 'Unknown')
                text = segment.get('text', '').strip()
                
                if speaker != current_speaker:
                    # Output previous speaker's text
                    if current_speaker is not None and current_text:
                        formatted_lines.append(f"[{current_speaker}]: {' '.join(current_text)}")
                    current_speaker = speaker
                    current_text = [text] if text else []
                else:
                    if text:
                        current_text.append(text)
            
            # Output last speaker's text
            if current_speaker is not None and current_text:
                formatted_lines.append(f"[{current_speaker}]: {' '.join(current_text)}")
            
            return '\n\n'.join(formatted_lines) if formatted_lines else data.get('text', '')
            
        except Exception as e:
            logger.warning(f"Failed to parse diarized response: {e}")
            # Fallback: try to get text directly
            if hasattr(response, 'text'):
                return response.text
            return str(response)

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
        model = transcription.get('model', 'whisper-1')

        formatted = f"**Transcription of `{file_name}`**\n\n"
        if model == DIARIZE_MODEL:
            formatted += "*Speaker Diarization Enabled*\n\n"
        if language and language != 'auto':
            formatted += f"*Language: {language}*\n\n"
        formatted += f"{text}"

        return formatted
