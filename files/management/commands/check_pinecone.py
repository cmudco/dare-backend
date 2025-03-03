from django.core.management.base import BaseCommand
from core.helpers.pinecone import PineconeClient
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Check Pinecone index status'

    def add_arguments(self, parser):
        parser.add_argument('--user_id', type=int, help='User ID to check namespace for')

    def handle(self, *args, **options):
        try:
            client = PineconeClient()

            index_stats = client.index.describe_index_stats()
            self.stdout.write(self.style.SUCCESS(f'Index stats: {index_stats}'))

            user_id = options.get('user_id')
            if user_id:
                try:
                    user = User.objects.get(id=user_id)
                    self.stdout.write(f'Checking namespace for user: {user.email}')

                    namespace = f'user_{user_id}'
                    stats = client.index.describe_index_stats()
                    namespace_vectors = stats.get('namespaces', {}).get(namespace, {}).get('vector_count', 0)

                    self.stdout.write(self.style.SUCCESS(f'Namespace {namespace} has {namespace_vectors} vectors'))

                except User.DoesNotExist:
                    self.stdout.write(self.style.ERROR(f'User with ID {user_id} not found'))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error checking Pinecone: {str(e)}'))