import json
from django_rq import enqueue, get_queue

from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

from core.services.document_processor import DocumentProcessor
from common.permissions import IsOwner
from ..tasks import process_file_embeddings
from ..models import File, Tag
from .serializers import FileSerializer, TagSerializer
from ..constants import ALLOWED_FILES, FileStatus
import logging
logger = logging.getLogger(__name__)


class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    parser_classes = (MultiPartParser, FormParser)

    def get_queryset(self):
        return File.active_objects.filter(
            user=self.request.user
        ).order_by('-id')

    def create(self, request):
        uploaded_files = request.FILES.getlist('files')
        file_names = request.data.getlist('names')

        if not uploaded_files:
            return Response({"error": "No files uploaded."}, status=status.HTTP_400_BAD_REQUEST)

        if len(uploaded_files) != len(file_names):
            return Response(
                {"error": "Number of files and names do not match."},
                status=status.HTTP_400_BAD_REQUEST
            )

        tags_data = request.data.get('tags', '[]')
        tag_ids = json.loads(tags_data)

        file_instances = []
        for idx, uploaded_file in enumerate(uploaded_files):
            file_name = file_names[idx]
            file_type = uploaded_file.content_type
            size = uploaded_file.size

            is_valid_file = True
            if size == 0:
                is_valid_file = False
            elif file_type and file_type.split('/')[-1] not in ALLOWED_FILES:
                is_valid_file = False

            data = {
                'file': uploaded_file,
                'name': file_name,
                'file_type': file_type,
                'size': size,
                'user': request.user.id,
                'status': FileStatus.FAILED if not is_valid_file else FileStatus.PROCESSING,
                'tags': tag_ids,
                'vector_db_source': request.user.vector_db
            }

            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            file_instance = serializer.save()

            if is_valid_file:
                try:
                    job = enqueue(process_file_embeddings, file_instance.id)
                    file_instance.job_id = job.id
                    file_instance.save(update_fields=['job_id'])
                except Exception as e:
                    file_instance.status = FileStatus.FAILED
                    file_instance.save(update_fields=['status'])
                    return Response(
                        {"error": f"Error processing file '{file_name}': {str(e)}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )

            file_instances.append(file_instance)

        serializer = self.get_serializer(file_instances, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='job-statuses', parser_classes=[JSONParser])
    def get_job_statuses(self, request):
        try:
            file_ids = request.data.get('fileIds', [])
            if not file_ids:
                return Response({"error": "No file IDs provided"}, status=status.HTTP_400_BAD_REQUEST)

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
                        error_message = str(job.exc_info) if job.exc_info else "Unknown error"
                        status_data["error"] = error_message
                        logger.error(f"Job failed for file ID {file.id}: {error_message}")

                if file.status == FileStatus.FAILED and "error" not in status_data:
                    status_data["error"] = "File processing failed"
                    logger.error(f"File with ID {file.id} has failed status but no error message")

                response_data.append(status_data)

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Error in get_job_statuses: {str(e)}")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user).union(Tag.objects.filter(user=None)).order_by('label')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)