import logging
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from core.services.document_processor import DocumentProcessor
from files.api.permissions import IsOwner
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
            processor = DocumentProcessor()
            processor.create_file_embeddings(file_instance)
        except Exception as e:
            raise Exception(f"Error processing file: {str(e)}")

class TagViewSet(viewsets.ModelViewSet):
    serializer_class = TagSerializer
    permission_classes = [IsAuthenticated, IsOwner]

    def get_queryset(self):
        return Tag.objects.filter(user=self.request.user).union(Tag.objects.filter(user=None)).order_by('label')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)