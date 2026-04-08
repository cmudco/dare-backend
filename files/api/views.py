import json
import logging
import mimetypes
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import BooleanField, Count, Exists, OuterRef, Prefetch, Q, Value
from django.db.models.functions import Lower
from django.http import FileResponse, Http404
from django_rq import enqueue, get_queue
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.permissions import IsOwner
from conversations.models import Conversation
from core.models import DareConfig
from core.services.document_processor import DocumentProcessor
from core.services.file_processor import FileProcessor
from core.services.file_upload_service import FileUploadService
from core.storage.backends import SyftBoxStorage
from core.storage.constants import StorageBackendChoice
from core.storage.permission_service import SyftBoxPermissionService
from syftbox.services.syftbox_file_service import SyftBoxFileService
from syftbox.services.syftbox_permission_service import (
    SyftBoxPermissionService as SyftBoxApiPermissionService,
)

from ..constants import ALLOWED_FILES, FileStatus
from ..models import File, FileShare, Folder, Tag
from .serializers import (
    FileSerializer,
    FileShareSerializer,
    FolderSerializer,
    TagSerializer,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_queryset(self):
        return (
            File.active_objects.filter(user=self.request.user)
            .annotate(
                is_shared_by_me=Exists(FileShare.objects.filter(file=OuterRef("pk"))),
                is_shared_publicly=Exists(
                    FileShare.objects.filter(file=OuterRef("pk"), shared_with=None)
                ),
            )
            .order_by(Lower("name"))
        )

    def create(self, request):
        uploaded_files = request.FILES.getlist("files")
        file_names = request.data.getlist("names")

        if not uploaded_files:
            return Response(
                {"error": "No files uploaded."}, status=status.HTTP_400_BAD_REQUEST
            )

        tags_data = request.data.get("tags", "[]")
        tag_ids = FileUploadService.parse_tags(tags_data)
        chunk_size = request.data.get("chunk_size")
        overlap_size = request.data.get("overlap_size")

        try:
            file_instances = FileUploadService.upload_files(
                uploaded_files,
                file_names,
                request.user,
                tag_ids,
                chunk_size=chunk_size,
                overlap_size=overlap_size,
            )
            serializer = self.get_serializer(file_instances, many=True)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Error uploading files: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(
        detail=False,
        methods=["post"],
        url_path="job-statuses",
        parser_classes=[JSONParser],
    )
    def get_job_statuses(self, request):
        try:
            file_ids = request.data.get("fileIds", [])
            if not file_ids:
                return Response(
                    {"error": "No file IDs provided"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            files = File.active_objects.filter(id__in=file_ids, user=request.user)
            queue = get_queue()

            response_data = []
            for file in files:
                job = queue.fetch_job(file.job_id) if file.job_id else None
                status_data = {
                    "fileId": file.id,
                    "jobId": file.job_id,
                    "status": file.get_status_display(),
                    "statusCode": file.status,
                }
                if job:
                    status_data["jobStatus"] = job.get_status()
                    if job.is_failed:
                        error_message = (
                            str(job.exc_info) if job.exc_info else "Unknown error"
                        )
                        status_data["error"] = error_message
                        logger.error(
                            f"Job failed for file ID {file.id}: {error_message}"
                        )

                if file.status == FileStatus.FAILED:
                    status_data["error"] = "File processing failed"
                    if file.error_message:
                        status_data["errorDetails"] = file.error_message
                    else:
                        logger.error(
                            f"File with ID {file.id} has failed status but no error message"
                        )

                response_data.append(status_data)

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in get_job_statuses: {str(e)}")
            return Response(
                {"error": "Internal server error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=False, methods=["post"], url_path="move")
    def move_files_to_folder(self, request):
        file_ids = request.data.get("fileIds", [])
        folder_id = request.data.get("folderId")

        if not file_ids:
            return Response(
                {"error": "No file IDs provided."}, status=status.HTTP_400_BAD_REQUEST
            )

        files = File.active_objects.filter(id__in=file_ids, user=request.user)

        if folder_id:
            try:
                folder = Folder.objects.get(id=folder_id, user=request.user)
                folder.files.add(*files)
                return Response(
                    {"status": "Files moved to folder successfully"},
                    status=status.HTTP_200_OK,
                )
            except Folder.DoesNotExist:
                return Response(
                    {"error": "Folder not found."}, status=status.HTTP_404_NOT_FOUND
                )
        else:
            for file in files:
                file.folders.clear()
            return Response(
                {"status": "Files removed from all folders"}, status=status.HTTP_200_OK
            )

    @action(detail=False, methods=["get"], url_path="by-owner/(?P<owner_id>[^/.]+)")
    def get_files_by_owner(self, request, owner_id=None):
        """
        Get files owned by a specific user (for forked conversations).

        Returns files that are associated with published conversations.
        This ensures users can only access files from shared conversations.
        """
        try:
            owner_id = int(owner_id)
        except (ValueError, TypeError):
            return Response(
                {"error": "Invalid owner ID"}, status=status.HTTP_400_BAD_REQUEST
            )

        all_file_ids = set()

        # Get files from published conversations
        published_conversations = Conversation.active_objects.filter(
            user_id=owner_id, is_published=True
        ).values_list("selected_file_ids", "selected_embedding_ids")

        for selected_file_ids, selected_embedding_ids in published_conversations:
            if selected_file_ids:
                all_file_ids.update(selected_file_ids)
            if selected_embedding_ids:
                all_file_ids.update(selected_embedding_ids)

        if not all_file_ids:
            return Response({"results": []}, status=status.HTTP_200_OK)

        files = (
            File.active_objects.filter(id__in=all_file_ids, user_id=owner_id)
            .annotate(
                is_shared_by_me=Exists(FileShare.objects.filter(file=OuterRef("pk"))),
                is_shared_publicly=Exists(
                    FileShare.objects.filter(file=OuterRef("pk"), shared_with=None)
                ),
            )
            .order_by(Lower("name"))
        )

        serializer = self.get_serializer(files, many=True)
        return Response({"results": serializer.data}, status=status.HTTP_200_OK)

    @action(
        detail=False,
        methods=["post"],
        url_path="bulk-delete",
        parser_classes=[JSONParser],
    )
    def bulk_delete(self, request):
        """
        Bulk delete multiple files using DRF's built-in delete method for each file.
        Expected payload: {"fileIds": [1, 2, 3, ...]}
        """
        file_ids = request.data.get("fileIds", [])

        if not file_ids:
            return Response(
                {"error": "No file IDs provided."}, status=status.HTTP_400_BAD_REQUEST
            )

        if not isinstance(file_ids, list):
            return Response(
                {"error": "fileIds must be a list."}, status=status.HTTP_400_BAD_REQUEST
            )

        files = File.active_objects.filter(id__in=file_ids, user=request.user)

        if not files.exists():
            return Response(
                {"error": "No valid files found to delete."},
                status=status.HTTP_404_NOT_FOUND,
            )

        deleted_files = []
        failed_files = []

        for file in files:
            try:
                file_data = {"id": file.id, "name": file.name}
                self.perform_destroy(file)
                deleted_files.append(file_data)
            except Exception as e:
                logger.error(f"Error deleting file ID {file.id}: {str(e)}")
                failed_files.append({"id": file.id, "error": str(e)})

        response_data = {
            "status": "Bulk delete completed",
            "deleted_count": len(deleted_files),
            "failed_count": len(failed_files),
            "requested_count": len(file_ids),
        }

        if failed_files:
            response_data["failed_files"] = failed_files

        return Response(response_data, status=status.HTTP_200_OK)

    # -------------------------------------------------------------------------
    # SyftBox File Sharing Actions
    # -------------------------------------------------------------------------

    def _get_syftbox_file_path(self, file_obj):
        """Return the SyftBox filesystem path for a file."""
        # SyftBoxClientWrapper usage intentionally disabled.
        return file_obj.file.path

    def _set_syftbox_read_permissions(self, owner_user, file_obj, readers):
        """Apply SyftBox read ACL for a file using the provided reader emails."""
        storage = SyftBoxStorage(user_email=owner_user.email)
        token, acl_path, pattern = storage.build_permission_context(file_obj.name)
        SyftBoxApiPermissionService(owner_email=owner_user.email).set_read_permissions(
            access_token=token,
            acl_path=acl_path,
            pattern=pattern,
            readers=readers,
        )

    def _get_import_access_token(self, request, is_publicly_shared):
        """Resolve SyftBox token for import reads."""
        if is_publicly_shared:
            return DareConfig.active_objects.first().access_token
        return request.user.access_token

    @action(
        detail=True,
        methods=["post", "delete"],
        url_path="share",
        parser_classes=[JSONParser],
    )
    def share(self, request, pk=None):
        """
        POST  – Share a SyftBox file with a specific user by email.
                Payload: {"email": "alex.chen@example.com"}
                Response: {"detail": "File shared successfully."}

        DELETE – Remove a specific user's access by email.
                 Payload: {"email": "alex.chen@example.com"}
                 Response: {"detail": "Access removed successfully."}
        """
        if request.method == "POST":
            return self._share_with_user(request, pk)
        return self._unshare_from_user(request, pk)

    def _share_with_user(self, request, pk):
        file_obj = self.get_object()

        if file_obj.storage_backend != StorageBackendChoice.SYFTBOX:
            return Response(
                {"error": "Only SyftBox files can be shared."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        email = request.data.get("email")
        if not email:
            return Response(
                {"error": "'email' field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            shared_with_user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"error": "No user found with that email."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if shared_with_user == request.user:
            return Response(
                {"error": "You cannot share a file with yourself."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _, created = FileShare.objects.get_or_create(
            file=file_obj,
            shared_with=shared_with_user,
            defaults={"shared_by": request.user},
        )
        if not created:
            return Response(
                {"error": "File is already shared with this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            all_shared_emails = list(
                FileShare.objects.filter(file=file_obj, shared_with__isnull=False)
                .values_list("shared_with__email", flat=True)
                .distinct()
            )
            self._set_syftbox_read_permissions(
                owner_user=request.user, file_obj=file_obj, readers=all_shared_emails
            )
        except Exception as e:
            logger.error(
                f"SyftBox permission update failed for file {file_obj.id}: {e}"
            )

        return Response(
            {"detail": "File shared successfully."}, status=status.HTTP_201_CREATED
        )

    def _unshare_from_user(self, request, pk):
        file_obj = self.get_object()

        email = request.data.get("email")
        if not email:
            return Response(
                {"error": "'email' field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            shared_with_user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response(
                {"error": "No user found with that email."},
                status=status.HTTP_404_NOT_FOUND,
            )

        deleted, _ = FileShare.objects.filter(
            file=file_obj, shared_with=shared_with_user
        ).delete()

        if not deleted:
            return Response(
                {"error": "Share record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            remaining_shared_emails = list(
                FileShare.objects.filter(file=file_obj, shared_with__isnull=False)
                .values_list("shared_with__email", flat=True)
                .distinct()
            )
            self._set_syftbox_read_permissions(
                owner_user=request.user,
                file_obj=file_obj,
                readers=remaining_shared_emails,
            )
        except Exception as e:
            logger.error(
                f"SyftBox permission revoke failed for file {file_obj.id}: {e}"
            )

        return Response(
            {"detail": "Access removed successfully."}, status=status.HTTP_200_OK
        )

    @action(
        detail=True,
        methods=["patch"],
        url_path="share-public",
        parser_classes=[JSONParser],
    )
    def share_public(self, request, pk=None):
        """
        Toggle public (everyone) sharing on or off for a SyftBox file.

        Payload:  {"share_with_everyone": true|false}
        Response: Full updated file object with is_shared_by_me and is_shared_publicly.
        """
        file_obj = self.get_object()

        if file_obj.storage_backend != StorageBackendChoice.SYFTBOX:
            return Response(
                {"error": "Only SyftBox files can be shared publicly."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Accept both snake_case and camelCase from the client
        share_with_everyone = request.data.get(
            "share_with_everyone", request.data.get("shareWithEveryone")
        )
        if share_with_everyone is None:
            return Response(
                {"error": "'share_with_everyone' field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        permission_service = SyftBoxPermissionService()

        if share_with_everyone:
            _, created = FileShare.objects.get_or_create(
                file=file_obj,
                shared_with=None,
                defaults={"shared_by": request.user},
            )
            if created:
                try:
                    config = DareConfig.active_objects.first()
                    self._set_syftbox_read_permissions(
                        owner_user=request.user,
                        file_obj=file_obj,
                        readers=[config.project_email],
                    )
                except Exception as e:
                    logger.error(
                        f"SyftBox everyone-grant failed for file {file_obj.id}: {e}"
                    )
        else:
            deleted, _ = FileShare.objects.filter(
                file=file_obj, shared_with=None
            ).delete()
            if deleted:
                try:
                    permission_service.revoke_everyone_read_access(
                        self._get_syftbox_file_path(file_obj)
                    )
                except Exception as e:
                    logger.error(
                        f"SyftBox everyone-revoke failed for file {file_obj.id}: {e}"
                    )

        # Return refreshed file object with updated annotations
        refreshed = (
            File.active_objects.filter(pk=file_obj.pk)
            .annotate(
                is_shared_by_me=Exists(FileShare.objects.filter(file=OuterRef("pk"))),
                is_shared_publicly=Exists(
                    FileShare.objects.filter(file=OuterRef("pk"), shared_with=None)
                ),
            )
            .first()
        )
        serializer = self.get_serializer(refreshed)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="shares")
    def shares(self, request, pk=None):
        """
        List all users a file has been specifically shared with (excludes public share).

        Response: {"shares": [{"id": 7, "email": "...", "name": "Alex Chen"}]}
        """
        file_obj = self.get_object()
        specific_shares = FileShare.objects.filter(
            file=file_obj, shared_with__isnull=False
        ).select_related("shared_with")

        shares_data = []
        for share in specific_shares:
            u = share.shared_with
            shares_data.append(
                {
                    "id": u.id,
                    "email": u.email,
                    "name": u.get_full_name().strip() or u.email,
                }
            )

        return Response({"shares": shares_data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="shared")
    def shared(self, request):
        """
        List SyftBox files shared with the current user (directly or publicly).

        Each file entry includes a `shared_by` object (null when shared publicly).
        """
        # Fetch all relevant share records in one query
        share_qs = (
            FileShare.objects.filter(Q(shared_with=request.user) | Q(shared_with=None))
            .exclude(shared_by=request.user)
            .select_related("shared_by")
        )

        # Build file_id → share mapping; prefer direct share over public share
        file_share_map = {}
        for share in share_qs:
            fid = share.file_id
            if fid not in file_share_map or share.shared_with is not None:
                file_share_map[fid] = share

        files = (
            File.active_objects.filter(id__in=file_share_map.keys())
            .annotate(
                is_shared_by_me=Value(False, output_field=BooleanField()),
                is_shared_publicly=Exists(
                    FileShare.objects.filter(file=OuterRef("pk"), shared_with=None)
                ),
            )
            .order_by(Lower("name"))
        )

        results = []
        for file in files:
            file_data = self.get_serializer(file).data
            share = file_share_map.get(file.id)
            if share and share.shared_with is not None:
                sharer = share.shared_by
                full_name = sharer.get_full_name().strip() or sharer.email
                name_parts = full_name.split()
                initials = "".join(p[0].upper() for p in name_parts if p)[:2]
                file_data["shared_by"] = {
                    "id": sharer.id,
                    "name": full_name,
                    "initials": initials,
                }
            else:
                file_data["shared_by"] = None
            results.append(file_data)

        return Response({"results": results}, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="import")
    def import_file(self, request, pk=None):
        """
        Import (copy) a shared SyftBox file into the current user's account.

        Creates a new File record in the importer's SyftBox storage with
        source_file set to the original for lineage tracking.
        Re-enqueues embedding processing for the copy.
        """
        from django.core.files.base import ContentFile

        from files.tasks import process_file_embeddings

        if request.user.storage_backend != StorageBackendChoice.SYFTBOX:
            return Response(
                {"error": "Only users with SyftBox storage can import shared files."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            original = File.active_objects.get(pk=pk)
        except File.DoesNotExist:
            return Response(
                {"error": "File not found."}, status=status.HTTP_404_NOT_FOUND
            )

        if original.user == request.user:
            return Response(
                {"error": "You cannot import your own file."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify the requesting user has access via a FileShare record
        has_access = (
            FileShare.objects.filter(file=original)
            .filter(Q(shared_with=request.user) | Q(shared_with=None))
            .exists()
        )
        is_publicly_shared = FileShare.objects.filter(
            file=original, shared_with=None
        ).exists()

        if not has_access:
            return Response(
                {"error": "This file has not been shared with you."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            if original.storage_backend == StorageBackendChoice.SYFTBOX:
                owner_storage = SyftBoxStorage(user_email=original.user.email)
                syftbox_key = owner_storage._build_file_path(original.file.name)
                access_token = self._get_import_access_token(
                    request, is_publicly_shared
                )
                file_bytes = SyftBoxFileService().download(access_token, syftbox_key)
            else:
                original.file.open("rb")
                file_bytes = original.file.read()
                original.file.close()
        except Exception as e:
            logger.error(f"Failed to read source file {original.id}: {e}")
            return Response(
                {"error": "Could not read source file from storage."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        original_filename = os.path.basename(original.file.name)

        new_file = File(
            user=request.user,
            name=original.name,
            file_type=original.file_type,
            storage_backend=StorageBackendChoice.SYFTBOX,
            source_file=original,
            is_media=original.is_media,
            media_type=original.media_type,
            status=FileStatus.PROCESSING,
        )

        try:
            new_file.file.save(original_filename, ContentFile(file_bytes), save=False)
            new_file.size = len(file_bytes)
            new_file.save()
        except Exception as e:
            logger.error(
                f"Failed to save imported file for user {request.user.id}: {e}"
            )
            return Response(
                {"error": "Failed to save file to your storage."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if new_file.is_media:
            new_file.status = FileStatus.PROCESSED
            new_file.save(update_fields=["status"])
        else:
            job = enqueue(process_file_embeddings, new_file.id)
            new_file.job_id = job.id
            new_file.save(update_fields=["job_id"])

        new_file.is_shared_by_me = False
        new_file.is_shared_publicly = False
        serializer = self.get_serializer(new_file)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class FileViewAPIView(APIView):
    permission_classes = [IsAuthenticated, IsOwner]

    def get(self, request, file_id):
        """
        Serve the actual file for viewing with proper content type headers.

        Uses DynamicStorageFileField which automatically routes to the correct
        storage backend (local or SyftBox) based on the file's storage_backend field.
        """
        logger.info(f"File view request - User: {request.user}, File ID: {file_id}")

        try:
            # Get the file object with proper ownership check
            try:
                file_obj = File.active_objects.get(id=file_id, user=request.user)
            except File.DoesNotExist:
                # Fallback: allow access if file belongs to a published conversation
                file_obj = File.active_objects.filter(
                    id=file_id, message__conversation__is_published=True
                ).first()

                if not file_obj:
                    raise File.DoesNotExist()

            # Ensure file is processed successfully
            if file_obj.status != FileStatus.PROCESSED:
                return Response(
                    {"error": "File is not ready for viewing"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            file_name = file_obj.file.name

            content_type, _ = mimetypes.guess_type(file_name)
            if not content_type:
                ext = os.path.splitext(file_name)[1].lower()
                if ext == ".pdf":
                    content_type = "application/pdf"
                elif ext in [".txt", ".md"]:
                    content_type = "text/plain"
                elif ext in [".docx", ".doc"]:
                    content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                else:
                    content_type = "application/octet-stream"

            try:
                if file_obj.storage_backend == StorageBackendChoice.SYFTBOX:
                    file_obj.file.open("rb")
                else:
                    if not file_obj.file.exists():
                        raise FileNotFoundError("File not found in storage")
                    file_obj.file.open("rb")
            except Exception as exc:
                raise FileNotFoundError("File not found in storage") from exc

            # Create file response
            response = FileResponse(
                file_obj.file, content_type=content_type, as_attachment=False
            )

            # Set CORS headers
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "GET"
            response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"

            # Set filename
            filename = file_obj.name or os.path.basename(file_name)
            response["Content-Disposition"] = f'inline; filename="{filename}"'

            return response

        except File.DoesNotExist:
            return Response(
                {"error": "File not found"}, status=status.HTTP_404_NOT_FOUND
            )
        except FileNotFoundError:
            return Response(
                {"error": "File not found in storage"},
                status=status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            logger.error(f"Error in FileViewAPIView: {str(e)}")
            return Response(
                {"error": f"Error accessing file: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        user = self.request.user
        return (
            Tag.objects.filter(Q(user=user) | Q(user=None))
            .annotate(
                file_count=Count(
                    "files",
                    filter=Q(
                        files__user=user, files__is_deleted=False, files__is_active=True
                    ),
                )
            )
            .order_by("label")
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class FolderViewSet(viewsets.ModelViewSet):
    serializer_class = FolderSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_queryset(self):
        return (
            Folder.objects.filter(user=self.request.user)
            .prefetch_related(
                Prefetch("files", queryset=File.active_objects.prefetch_related("tags"))
            )
            .annotate(
                file_count=Count(
                    "files", filter=Q(files__is_deleted=False, files__is_active=True)
                )
            )
            .order_by(Lower("name"))
        )

    def create(self, request):
        """
        Create a folder and optionally upload files to it.
        Supports both:
        1. Creating empty folder: {"name": "folder_name"}
        2. Creating folder with files: {"name": "folder_name", "files": [...], "names": [...]}
        """
        folder_name = request.data.get("name")
        if not folder_name:
            return Response(
                {"error": "Folder name is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if Folder.objects.filter(name=folder_name, user=request.user).exists():
            return Response(
                {"error": "A folder with this name already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        uploaded_files = (
            request.FILES.getlist("files") if hasattr(request.FILES, "getlist") else []
        )
        file_names = (
            request.data.get("names", [])
            if isinstance(request.data.get("names"), list)
            else (
                request.data.getlist("names", [])
                if hasattr(request.data, "getlist")
                else []
            )
        )

        if not uploaded_files:
            folder = Folder.objects.create(name=folder_name, user=request.user)
            serializer = self.get_serializer(folder)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        tags_data = request.data.get("tags", "[]")
        if isinstance(tags_data, list):
            tags_data = json.dumps(tags_data)
        tag_ids = FileUploadService.parse_tags(tags_data)

        try:
            folder, file_instances = FileUploadService.upload_folder_with_files(
                folder_name, uploaded_files, file_names, request.user, tag_ids
            )
            serializer = self.get_serializer(folder)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Error creating folder with files: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="add-files")
    def add_files(self, request, pk=None):
        folder = self.get_object()
        file_ids = request.data.get("fileIds", [])

        files = File.active_objects.filter(id__in=file_ids, user=request.user)
        folder.files.add(*files)

        return Response({"status": "files added to folder"})

    @action(detail=True, methods=["post"], url_path="remove-files")
    def remove_files(self, request, pk=None):
        folder = self.get_object()
        file_ids = request.data.get("fileIds", [])

        files = File.active_objects.filter(id__in=file_ids, user=request.user)
        folder.files.remove(*files)

        return Response({"status": "files removed from folder"})


# ============================================================================
# Internal API - Inter-service Communication
# ============================================================================


class InternalFileUploadView(APIView):
    """
    Internal endpoint for inter-service file upload.

    Allows socraticbooks-backend to upload files on behalf of users without
    requiring a user JWT token. Used for webhook-triggered transcript uploads.

    Authenticated via X-Internal-Key header (shared secret).
    """

    permission_classes = [AllowAny]
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request) -> Response:
        """
        Upload file(s) on behalf of a user.

        Request body:
            - user_id: Target user's ID (required)
            - files: File(s) to upload (required)
            - names: Filename(s) for the uploaded files (optional)
            - tags: JSON array of tag IDs (optional)
            - tag_labels: JSON array of tag label strings (optional, creates tags if needed)

        Returns:
            List of created file objects
        """
        # Verify internal key
        internal_key = request.headers.get("X-Internal-Key", "")
        expected_key = getattr(settings, "DARE_INTERNAL_KEY", "")

        if not internal_key or internal_key != expected_key:
            logger.warning("Internal file upload: Invalid or missing internal key")
            return Response({"error": "Unauthorized"}, status=status.HTTP_403_FORBIDDEN)

        # Get target user
        user_id = request.data.get("user_id")
        if not user_id:
            return Response(
                {"error": "user_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {"error": f"User with id {user_id} not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Get uploaded files
        uploaded_files = request.FILES.getlist("files")
        if not uploaded_files:
            return Response(
                {"error": "No files uploaded"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Get optional parameters
        file_names = (
            request.data.getlist("names") if hasattr(request.data, "getlist") else []
        )
        tags_data = request.data.get("tags", "[]")
        tag_ids = FileUploadService.parse_tags(tags_data)

        # Support tag_labels: create-or-get tags by label string
        tag_labels_data = request.data.get("tag_labels", "[]")
        try:
            tag_labels = json.loads(tag_labels_data) if tag_labels_data else []
        except json.JSONDecodeError:
            tag_labels = []
        for label in tag_labels:
            tag, _ = Tag.objects.get_or_create(label=label, defaults={"user": user})
            if tag.id not in tag_ids:
                tag_ids.append(tag.id)

        try:
            file_instances = FileUploadService.upload_files(
                uploaded_files, file_names, user, tag_ids
            )
            serializer = FileSerializer(file_instances, many=True)
            logger.info(
                f"Internal file upload: {len(file_instances)} file(s) "
                f"uploaded for user {user_id}"
            )
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Internal file upload error: {str(e)}")
            return Response(
                {"error": f"Error uploading files: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
