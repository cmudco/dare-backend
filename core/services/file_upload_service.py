import json
import logging
import base64
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime
from django.db import transaction
from django.core.files.base import ContentFile
from django_rq import enqueue

from core.storage.constants import StorageBackendChoice
from core.storage.storage_service import get_storage_for_user
from core.storage.permission_service import SyftBoxPermissionService
from core.storage.syftbox_client import SyftBoxClientWrapper
from files.models import File, Folder, Tag
from files.constants import ALLOWED_FILES, FileStatus
from files.tasks import process_file_embeddings

logger = logging.getLogger(__name__)


# Media file MIME type prefixes
MEDIA_MIME_TYPES = {
    'image': ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/bmp', 'image/tiff', 'image/svg+xml'],
    'video': ['video/mp4', 'video/webm', 'video/quicktime', 'video/x-msvideo', 'video/mpeg', 'video/ogg'],
    'audio': ['audio/mpeg', 'audio/wav', 'audio/x-wav', 'audio/mp4', 'audio/x-m4a', 'audio/flac', 'audio/aac', 'audio/x-ms-wma', 'audio/opus', 'audio/ogg']
}


class FileUploadService:
    """
    Service for handling file uploads with validation and background processing.
    """

    @staticmethod
    def detect_media_type(content_type: str) -> tuple[bool, Optional[str]]:
        """
        Detect if file is a media file (image/video/audio) and return its type.

        Args:
            content_type: MIME type of the file

        Returns:
            tuple: (is_media, media_type) where media_type is 'image', 'video', 'audio', or None
        """
        if not content_type:
            return False, None

        for media_type, mime_types in MEDIA_MIME_TYPES.items():
            if content_type in mime_types or any(content_type.startswith(mt.split('/')[0] + '/') for mt in mime_types):
                return True, media_type

        return False, 'document'

    @staticmethod
    def validate_file(uploaded_file, file_name: str) -> tuple[bool, Optional[str]]:
        """
        Validate uploaded file.

        Args:
            uploaded_file: Django uploaded file object
            file_name: Name of the file

        Returns:
            tuple: (is_valid, error_message)
        """
        if uploaded_file.size == 0:
            return False, "Empty file not allowed"

        file_type = uploaded_file.content_type
        if file_type and file_type.split('/')[-1] not in ALLOWED_FILES:
            return False, f"File type {file_type} not allowed"

        return True, None

    @staticmethod
    def create_file_instance(uploaded_file, file_name: str, user, tag_ids: List[int] = None, *, chunk_size: int | None = None, overlap_size: int | None = None) -> File:
        """
        Create a file instance in the database.

        Args:
            uploaded_file: Django uploaded file object
            file_name: Name of the file
            user: User instance
            tag_ids: List of tag IDs to associate with the file

        Returns:
            File instance
        """
        is_valid, error_message = FileUploadService.validate_file(uploaded_file, file_name)

        # Detect if this is a media file (image/video)
        is_media, media_type = FileUploadService.detect_media_type(uploaded_file.content_type)

        # Media files should be marked as PROCESSED immediately (no vectorization needed)
        # Document files go through PROCESSING status and background job
        file_status = FileStatus.FAILED if not is_valid else (FileStatus.PROCESSED if is_media else FileStatus.PROCESSING)

        # Get storage backend based on user preference
        storage_backend = getattr(user, 'storage_backend', StorageBackendChoice.LOCAL)
        storage = get_storage_for_user(user)

        # Save file using the appropriate storage backend
        saved_name = storage.save(f'files/{file_name}', uploaded_file)

        # Generate syft_url if using SyftBox storage
        syft_url = None
        if storage_backend == StorageBackendChoice.SYFTBOX:
            client = SyftBoxClientWrapper(user.email)
            syft_url = client.get_syft_url(user.email, f'files/{file_name}')

            # Set file permissions for owner
            permission_service = SyftBoxPermissionService()
            permission_service.set_file_permissions(
                file_path=Path(storage.path(saved_name)),
                owner_email=user.email
            )

        file_data = {
            'file': saved_name,
            'name': file_name,
            'file_type': uploaded_file.content_type,
            'size': uploaded_file.size,
            'user': user,
            'status': file_status,
            'vector_db_source': user.vector_db if not is_media else None,  # No vector DB for media files
            'is_media': is_media,
            'media_type': media_type,
            'storage_backend': storage_backend,
            'syft_url': syft_url,
        }

        file_instance = File.active_objects.create(**file_data)

        if tag_ids:
            tags = Tag.objects.filter(id__in=tag_ids)
            file_instance.tags.add(*tags)

        # Only queue background processing job for non-media files (documents)
        if is_valid and not is_media:
            try:
                job = enqueue(process_file_embeddings, file_instance.id, chunk_size, overlap_size)
                file_instance.job_id = job.id
                file_instance.save(update_fields=['job_id'])
                logger.info(f"Queued document processing for file '{file_name}'")
            except Exception as e:
                file_instance.status = FileStatus.FAILED
                file_instance.save(update_fields=['status'])
                logger.error(f"Error processing file '{file_name}': {str(e)}")
                raise Exception(f"Error processing file '{file_name}': {str(e)}")
        elif is_media:
            logger.info(f"Media file '{file_name}' ({media_type}) uploaded successfully - skipping vectorization")

        return file_instance

    @staticmethod
    def upload_files(uploaded_files: List, file_names: List[str], user, tag_ids: List[int] = None, *, chunk_size: int | None = None, overlap_size: int | None = None) -> List[File]:
        """
        Upload multiple files and create file instances.

        Args:
            uploaded_files: List of Django uploaded file objects
            file_names: List of file names
            user: User instance
            tag_ids: List of tag IDs to associate with files

        Returns:
            List of File instances
        """
        if len(uploaded_files) != len(file_names):
            raise ValueError("Number of files and names do not match")

        file_instances = []

        with transaction.atomic():
            for idx, uploaded_file in enumerate(uploaded_files):
                file_name = file_names[idx]
                try:
                    file_instance = FileUploadService.create_file_instance(
                        uploaded_file, file_name, user, tag_ids,
                        chunk_size=chunk_size, overlap_size=overlap_size
                    )
                    file_instances.append(file_instance)
                except Exception as e:
                    logger.error(f"Failed to upload file {file_name}: {str(e)}")
                    raise

        return file_instances

    @staticmethod
    def upload_folder_with_files(folder_name: str, uploaded_files: List, file_names: List[str],
                                user, tag_ids: List[int] = None, *, chunk_size: int | None = None, overlap_size: int | None = None) -> tuple[Folder, List[File]]:
        """
        Create a folder and upload files to it.

        Args:
            folder_name: Name of the folder
            uploaded_files: List of Django uploaded file objects
            file_names: List of file names
            user: User instance
            tag_ids: List of tag IDs to associate with files

        Returns:
            tuple: (Folder instance, List of File instances)
        """
        with transaction.atomic():
            # Create folder
            folder = Folder.objects.create(name=folder_name, user=user)

            # Upload files
            file_instances = FileUploadService.upload_files(
                uploaded_files, file_names, user, tag_ids,
                chunk_size=chunk_size, overlap_size=overlap_size
            )

            # Add files to folder
            folder.files.add(*file_instances)

        return folder, file_instances

    @staticmethod
    def parse_tags(tags_data: str) -> List[int]:
        """
        Parse tags data from JSON string.

        Args:
            tags_data: JSON string containing tag IDs

        Returns:
            List of tag IDs
        """
        try:
            return json.loads(tags_data) if tags_data else []
        except json.JSONDecodeError:
            logger.warning(f"Invalid tags data: {tags_data}")
            return []

    @staticmethod
    def save_base64_image(
        base64_data: str,
        filename: str,
        mime_type: str,
        user=None,
        is_public: bool = False
    ) -> Optional[File]:
        """
        Save a base64-encoded image as a File object.
        Used for saving images uploaded via websocket in chat.

        Args:
            base64_data: Base64 encoded image data (can include data URL prefix)
            filename: Original filename
            mime_type: MIME type of the image (e.g., 'image/jpeg')
            user: User who uploaded the image (None for public bots)
            is_public: Whether this is for a public bot conversation

        Returns:
            File object if successful, None otherwise
        """
        try:
            # Extract base64 data if it includes a data URL prefix
            # Format: data:image/jpeg;base64,/9j/4AAQ...
            if ',' in base64_data:
                # Extract MIME type from data URL if present
                header, base64_data = base64_data.split(',', 1)
                if 'data:' in header and ';base64' in header:
                    extracted_mime = header.replace('data:', '').replace(';base64', '')
                    if extracted_mime:
                        mime_type = extracted_mime

            # Decode base64 to bytes
            image_bytes = base64.b64decode(base64_data)

            # Generate a unique filename if needed
            if not filename:
                ext = mime_type.split('/')[-1] if mime_type else 'png'
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"chat_upload_{timestamp}_{uuid.uuid4().hex[:8]}.{ext}"

            # Detect media type
            is_media, media_type = FileUploadService.detect_media_type(mime_type)

            # Create File object
            file_obj = File(
                user=user,  # Can be None for public bots
                name=filename,
                file_type=mime_type,
                size=len(image_bytes),
                status=FileStatus.PROCESSED,  # Media files don't need processing
                is_media=is_media,
                media_type=media_type or 'image',
                is_generated=False,  # User-uploaded, not AI-generated
            )

            # Save the image file
            file_obj.file.save(filename, ContentFile(image_bytes), save=False)
            file_obj.save()

            log_msg = f"Saved uploaded image as File ID: {file_obj.id}, name: {filename}"
            if is_public:
                log_msg += " (public bot)"
            logger.info(log_msg)

            return file_obj

        except Exception as e:
            error_msg = f"Error saving uploaded image '{filename}': {str(e)}"
            logger.exception(error_msg)
            return None

    @staticmethod
    def save_base64_images(
        images: List[Dict[str, Any]],
        user=None,
        is_public: bool = False
    ) -> List[File]:
        """
        Save multiple base64-encoded images as File objects.

        Args:
            images: List of dicts with 'preview' (base64), 'name', and 'type' keys
            user: User who uploaded the images (None for public bots)
            is_public: Whether this is for a public bot conversation

        Returns:
            List of successfully saved File objects
        """
        saved_files = []

        for image_data in images:
            preview = image_data.get('preview', '')
            name = image_data.get('name', '')
            mime_type = image_data.get('type', 'image/png')

            if not preview:
                logger.warning(f"Skipping image with no preview data: {name}")
                continue

            file_obj = FileUploadService.save_base64_image(
                base64_data=preview,
                filename=name,
                mime_type=mime_type,
                user=user,
                is_public=is_public
            )

            if file_obj:
                saved_files.append(file_obj)

        return saved_files
