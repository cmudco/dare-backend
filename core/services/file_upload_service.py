import json
import logging
from typing import List, Dict, Any, Optional
from django.db import transaction
from django_rq import enqueue

from files.models import File, Folder, Tag
from files.constants import ALLOWED_FILES, FileStatus
from files.tasks import process_file_embeddings

logger = logging.getLogger(__name__)


class FileUploadService:
    """
    Service for handling file uploads with validation and background processing.
    """

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

        file_data = {
            'file': uploaded_file,
            'name': file_name,
            'file_type': uploaded_file.content_type,
            'size': uploaded_file.size,
            'user': user,
            'status': FileStatus.FAILED if not is_valid else FileStatus.PROCESSING,
            'vector_db_source': user.vector_db
        }

        file_instance = File.active_objects.create(**file_data)

        if tag_ids:
            tags = Tag.objects.filter(id__in=tag_ids)
            file_instance.tags.add(*tags)

        if is_valid:
            try:
                job = enqueue(process_file_embeddings, file_instance.id, chunk_size, overlap_size)
                file_instance.job_id = job.id
                file_instance.save(update_fields=['job_id'])
            except Exception as e:
                file_instance.status = FileStatus.FAILED
                file_instance.save(update_fields=['status'])
                logger.error(f"Error processing file '{file_name}': {str(e)}")
                raise Exception(f"Error processing file '{file_name}': {str(e)}")

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
