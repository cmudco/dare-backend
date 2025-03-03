from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from ..models import File
from .serializers import FileSerializer

class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def get_queryset(self):
        return File.objects.filter(user=self.request.user, is_deleted=False)

    def perform_create(self, serializer):
        uploaded_file = self.request.FILES.get('file')
        file_type = uploaded_file.content_type if uploaded_file else None
        size = uploaded_file.size if uploaded_file else None
        serializer.save(user=self.request.user, file_type=file_type, size=size)