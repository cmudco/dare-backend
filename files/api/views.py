import logging
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
from ..constants import ALLOWED_FILES

logger = logging.getLogger(__name__)

class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsAuthenticated, IsOwner]
    parser_classes = (MultiPartParser, FormParser)

    def get_queryset(self):
        return File.active_objects.filter(
            user=self.request.user
        ).order_by('-id')

    def perform_create(self, serializer):
        uploaded_file = self.request.FILES.get('file')
        file_type = uploaded_file.content_type if uploaded_file else None
        size = uploaded_file.size if uploaded_file else None

        if file_type and file_type.split('/')[-1] not in ALLOWED_FILES:
            raise ValidationError(f"File type '{file_type}' is not allowed. Allowed types are: {', '.join(ALLOWED_FILES)}")

        tag_ids = self.request.data.getlist('tags', [])

        if not tag_ids and 'tags' in self.request.data:
            try:
                tags_data = self.request.data.get('tags')
                if isinstance(tags_data, str):
                    import json
                    tag_ids = json.loads(tags_data)
                elif hasattr(tags_data, '__iter__'):
                    tag_ids = list(tags_data)
            except Exception:
                pass

        tag_ids = [int(tag_id) for tag_id in tag_ids if str(tag_id).isdigit()]

        tags = Tag.objects.filter(id__in=tag_ids) if tag_ids else []

        file_instance = serializer.save(
            user=self.request.user,
            file_type=file_type,
            size=size
        )

        if tags:
            file_instance.tags.add(*tags)

        try:
            job = enqueue(process_file_embeddings, file_instance.id)
            file_instance.job_id = job.id
            file_instance.save(update_fields=['job_id'])
        except Exception as e:
            raise Exception(f"Error processing file: {str(e)}")
        

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
                response_data.append(status_data)

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.exception(f"Error checking job statuses: {str(e)}")
            return Response({"error": "Internal server error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user).union(Tag.objects.filter(user=None)).order_by('label')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)