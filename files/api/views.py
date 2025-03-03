from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response

from core.services.document_processor import DocumentProcessor
from ..models import File
from .serializers import FileSerializer

class FileViewSet(viewsets.ModelViewSet):
    serializer_class = FileSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser)

    def get_queryset(self):
        return File.objects.filter(
            user=self.request.user,
            is_deleted=False
        ).order_by('-id')

    def perform_create(self, serializer):
        uploaded_file = self.request.FILES.get('file')
        file_type = uploaded_file.content_type if uploaded_file else None
        size = uploaded_file.size if uploaded_file else None
        file_instance = serializer.save(user=self.request.user, file_type=file_type, size=size)

        try:
            processor = DocumentProcessor()
            processor.process_file(file_instance)
        except Exception as e:
            print(f"Error processing file: {str(e)}")

    @action(detail=False, methods=['post'])
    def test_embeddings(self, request):
        """Test endpoint for embeddings search"""
        try:
            query = request.data.get('query')
            file_id = request.data.get('file_id')

            if not query:
                return Response({'error': 'Query is required'}, status=400)

            processor = DocumentProcessor()

            if file_id:
                try:
                    file = File.objects.get(id=file_id, user=request.user)
                    processor.process_file(file)
                except File.DoesNotExist:
                    return Response({'error': 'File not found'}, status=404)

            results = processor.search_similar_content(
                query_text=query,
                user_id=request.user.id,
                top_k=5
            )

            formatted_results = []
            for match in results:
                metadata = match.get('metadata', {})
                formatted_results.append({
                    'score': match.get('score'),
                    'file_id': metadata.get('file_id'),
                    'filename': metadata.get('filename'),
                    'text_preview': metadata.get('text', '')[:200] + '...' if len(metadata.get('text', '')) > 200 else metadata.get('text', '')
                })

            return Response({
                'query': query,
                'results': formatted_results
            })

        except Exception as e:
            return Response({'error': str(e)}, status=500)
