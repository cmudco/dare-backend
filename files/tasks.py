from django_rq import job
import time
from datetime import datetime

from core.services.document_processor import DocumentProcessor
from core.services.vector_service import get_vector_service
from .models import File
from .constants import FileStatus
from users.constants import VectorDBChoice

@job
def process_file_embeddings(file_id):
    start_time = time.time()

    try:
        file = File.active_objects.get(id=file_id)
    except File.DoesNotExist:
        return
    except Exception as e:
        return

    try:
        file.status = FileStatus.PROCESSING
        file.save(update_fields=['status'])

        DocumentProcessor().create_file_embeddings(file)

        # Record the user's current vector DB preference with the file
        file.vector_db_source = file.user.vector_db
        file.status = FileStatus.PROCESSED
        file.save(update_fields=['status', 'vector_db_source'])

        elapsed_time = time.time() - start_time

    except Exception as e:
        elapsed_time = time.time() - start_time
        try:
            file.status = FileStatus.FAILED
            file.save(update_fields=['status'])
        except Exception as update_error:
            pass

@job
def delete_file_vectors(file_id, user_id):
    """Delete file vectors from the correct vector DB."""
    try:
        # Try to get the file to check its vector_db_source
        try:
            file = File.active_objects.get(id=file_id)
            vector_db_source = file.vector_db_source
        except File.DoesNotExist:
            # File already deleted from DB, we'll have to try with current user preference
            vector_db_source = None

        # Get user and current preference
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(id=user_id)
        current_preference = user.vector_db

        if vector_db_source:
            # Temporarily set user's vector_db to match the file's source
            user.vector_db = vector_db_source
            user.save(update_fields=['vector_db'])

            # Delete vectors using correct vector DB
            processor = DocumentProcessor()
            processor.update_vector_service(user_id)
            result = processor.delete_file_vectors(file_id, user_id)

            # Reset user's preference
            user.vector_db = current_preference
            user.save(update_fields=['vector_db'])

        else:
            # For older files with no recorded source, default to current preference
            processor = DocumentProcessor()
            processor.update_vector_service(user_id)
            result = processor.delete_file_vectors(file_id, user_id)

    except Exception as e:
        pass

# @job("default", timeout=3600)
# def migrate_vector_db(user_id, target_vector_db, source_vector_db=None):
#     """
#     Migrate files from one vector DB to another when user changes preference.
#     This creates embeddings in the new DB while preserving the old ones.
#     """
#     try:
#         from django.contrib.auth import get_user_model
#         from users.constants import VectorDBChoice
#         User = get_user_model()
#
#         # Get user
#         user = User.objects.get(id=user_id)
#
#         # If source_vector_db is not provided, read it from the user
#         if source_vector_db is None:
#             source_vector_db = user.vector_db
#
#         # Get human-readable names for better logging
#         source_db_name = dict(VectorDBChoice.choices).get(source_vector_db, "Unknown")
#         target_db_name = dict(VectorDBChoice.choices).get(target_vector_db, "Unknown")
#
#         if source_vector_db == target_vector_db:
#             return True
#
#         # Temporarily set user's vector DB to target for creating new embeddings
#         user.vector_db = target_vector_db
#         user.save(update_fields=['vector_db'])
#
#         # Get all files that need migration (active and processed)
#         files = File.active_objects.filter(
#             user_id=user_id,
#             is_deleted=False,
#             status=FileStatus.PROCESSED
#         )
#
#         processor = DocumentProcessor()
#         processor.update_vector_service(user_id)
#
#         # Process each file to generate embeddings in target vector DB
#         processed_count = 0  # Initialize counter
#         for file in files:
#             try:
#                 # Create embeddings in new vector DB
#                 processor.create_file_embeddings(file)
#
#                 # Update file to record both vector DB sources
#                 file.vector_db_source = target_vector_db
#                 file.save(update_fields=['vector_db_source'])
#                 processed_count += 1
#
#             except Exception as e:
#                 continue
#
#         return True
#
#     except Exception as e:
#         return False