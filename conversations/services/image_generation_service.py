"""
Image Generation Service

Handles saving of AI-generated images (e.g., from DALL-E) as File objects.
Supports both authenticated users and public/anonymous bot conversations.
"""

import logging
from typing import Dict, Optional
from datetime import datetime
from django.core.files.base import ContentFile
from files.models import File
from files.constants import FileStatus
from core.storage.constants import StorageBackendChoice

logger = logging.getLogger(__name__)


class ImageGenerationService:
    """Service for handling AI-generated images."""

    @staticmethod
    def save_generated_image(
        image_bytes: bytes,
        prompt: str,
        metadata: Dict,
        user=None,
        is_public: bool = False
    ) -> Optional[File]:
        """
        Save AI-generated image as a File object.

        Args:
            image_bytes: The raw image bytes
            prompt: The original generation prompt
            metadata: Dictionary containing generation metadata (model, size, quality, etc.)
            user: User who requested the image (None for public bots)
            is_public: Whether this is for a public bot conversation

        Returns:
            File object if successful, None otherwise
        """
        try:
            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = "dalle_public" if is_public else "dalle_generated"
            filename = f"{prefix}_{timestamp}.png"

            storage_backend = StorageBackendChoice.LOCAL
            if user:
                storage_backend = getattr(user, 'storage_backend', StorageBackendChoice.LOCAL)

            # Create File object
            file_obj = File(
                user=user,  # Can be None for public bots
                name=filename,
                file_type="image/png",
                size=len(image_bytes),
                status=FileStatus.PROCESSED,
                is_media=True,
                media_type='generated_image',
                is_generated=True,
                generation_prompt=prompt,
                revised_prompt=metadata.get('revised_prompt', ''),
                generation_params={
                    'model': metadata.get('model', 'dall-e-3'),
                    'size': metadata.get('size', '1024x1024'),
                    'quality': metadata.get('quality', 'standard'),
                    'style': metadata.get('style', 'vivid'),
                },
                storage_backend=storage_backend,
            )

            # Save the image file
            file_obj.file.save(filename, ContentFile(image_bytes), save=False)
            file_obj.save()

            log_msg = f"Saved generated image as File ID: {file_obj.id}"
            if is_public:
                log_msg += " (public bot)"
            logger.info(log_msg)

            return file_obj

        except Exception as e:
            error_msg = f"Error saving generated image: {str(e)}"
            if is_public:
                error_msg = f"Error saving generated image (public): {str(e)}"
            logger.exception(error_msg)
            return None

    @staticmethod
    def extract_image_metadata(generated_image_data: Dict) -> Dict:
        """
        Extract and normalize image generation metadata.

        Args:
            generated_image_data: Raw image data from LLM service

        Returns:
            Normalized metadata dictionary
        """
        return {
            'model': generated_image_data.get('model', 'dall-e-3'),
            'size': generated_image_data.get('size', '1024x1024'),
            'quality': generated_image_data.get('quality', 'standard'),
            'style': generated_image_data.get('style', 'vivid'),
            'revised_prompt': generated_image_data.get('revised_prompt', ''),
        }
