from django.core.management.base import BaseCommand
from files.models import File
from core.services.document_processor import DocumentProcessor
from core.helpers.pinecone import PineconeClient

class Command(BaseCommand):
    help = 'Test embeddings generation and retrieval'

    def add_arguments(self, parser):
        parser.add_argument('file_id', type=int, help='ID of file to test')
        parser.add_argument('--query', type=str, default=None, help='Test query to search with')

    def handle(self, *args, **options):
        file_id = options['file_id']
        query = options['query']

        try:
            file = File.objects.get(id=file_id)
            self.stdout.write(self.style.SUCCESS(f'Found file: {file.name}'))

            if not query:
                processor = DocumentProcessor()
                self.stdout.write('Processing file...')
                result = processor.process_file(file)
                self.stdout.write(self.style.SUCCESS(f'File processed: {result}'))

            else:
                self.stdout.write(f'Searching for: "{query}"')
                processor = DocumentProcessor()
                results = processor.search_similar_content(
                    query_text=query,
                    user_id=file.user.id,
                    top_k=3
                )

                self.stdout.write(self.style.SUCCESS(f'Found {len(results)} results:'))
                for i, match in enumerate(results):
                    self.stdout.write(f"\n--- Result {i+1} ---")
                    self.stdout.write(f"Score: {match.get('score')}")
                    self.stdout.write(f"File: {match.get('metadata', {}).get('filename')}")
                    self.stdout.write(f"Text preview: {match.get('metadata', {}).get('text')[:100]}...")

        except File.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'File with ID {file_id} not found'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {str(e)}'))