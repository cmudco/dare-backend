# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with the Django backend of the DARE project.

## Project Architecture

This is a Django REST API backend for an AI-powered research and conversation platform with multi-LLM support, real-time WebSocket communication, and vector database integration.

## Development Commands

```bash
# Environment setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements/local.txt

# Development server
python manage.py runserver

# Background task worker (requires Redis)
redis-server
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES python -Wd manage.py rqworker default -v 3

# ASGI server (for WebSocket support)
uvicorn dare.asgi:application --port 8000 --reload --log-level debug

# Database operations
python manage.py migrate
python manage.py makemigrations

# Code formatting and linting
black .                    # Format code
black --check --verbose .  # Check formatting
isort .                    # Sort imports
isort . -c                 # Check import sorting
```

## Architecture Patterns

### Model Layer Patterns

**Base Model Hierarchy:**
- `TimeStampMixin`: Adds `created_at`, `updated_at` with auto-updating
- `IsActiveMixin`: Adds `is_active` field with `enable()`/`disable()` methods
- `IsDeletedMixin`: Adds `is_deleted` field with `soft_delete()`/`undelete()` methods
- `BaseModel`: Combines all mixins (use for most models)

**Custom Managers:**
- `ActiveObjectsManager`: Filters `is_active=True, is_deleted=False`
- Use `Model.active_objects` for filtered querysets
- Use `Model.objects` for all records

**Model Conventions:**
- Import constants from dedicated `constants.py` files per app
- Use `help_text` on all fields for documentation
- Use `verbose_name` for user-facing field names
- Implement `__str__` methods that return meaningful representations
- Use `related_name` for foreign keys and many-to-many fields

### View Layer Patterns

**ViewSet Structure:**
- Inherit from `viewsets.ModelViewSet` for full CRUD
- Override `get_queryset()` to filter by user: `Model.active_objects.filter(user=self.request.user)`
- Override `perform_create()` to set user: `serializer.save(user=self.request.user)`
- Use `@action` decorator for custom endpoints
- Always include `permission_classes = [IsAuthenticated]`

**Custom Actions Pattern:**
```python
@action(detail=False, methods=['patch'], url_path='custom-action')
def custom_action(self, request):
    # Custom logic here
    return Response(data, status=status.HTTP_200_OK)
```

### Serializer Layer Patterns

**Read/Write Field Separation:**
- Use `source` parameter for field mapping
- Use `write_only=True` for input-only fields (e.g., `password`)
- Use `read_only=True` for computed/foreign key fields
- Separate foreign key serialization:
  ```python
  prompt = PromptSerializer(read_only=True)
  prompt_id = serializers.PrimaryKeyRelatedField(
      queryset=Prompt.active_objects.all(),
      source='prompt',
      write_only=True
  )
  ```

### Service Layer Patterns

**Service Architecture:**
- Services live in `core/services/`
- Each service handles one domain (e.g., `OpenAIService`, `DocumentProcessor`)
- Abstract base classes for extensibility (`AIService` ABC)
- Dependency injection in service constructors

**LLM Integration Pattern:**
```python
class AIService(ABC):
    @abstractmethod
    async def stream_chat_completion(self, messages: list, max_tokens: int, temperature: float) -> AsyncGenerator[Tuple[str, Dict], None]:
        pass
```

**Background Task Pattern:**
```python
from django_rq import job

@job
def process_file_embeddings(file_id):
    # Processing logic
    file.status = FileStatus.PROCESSING
    file.save(update_fields=['status'])
```

### WebSocket Consumer Patterns

**Consumer Structure:**
- Inherit from `AsyncWebsocketConsumer`
- Validate user and conversation in `connect()`
- Use `database_sync_to_async` for database operations
- Handle streaming responses with async generators
- Proper error handling and cleanup in `disconnect()`

**WebSocket Message Format:**
```python
await self.send(text_data=json.dumps({
    'message': chunk,
    'usage': usage_data,
    'message_id': message.id
}))
```

## Key Integrations

### Vector Database Support
- **Weaviate & Pinecone**: Toggle via user preferences
- Service pattern: `get_vector_service()` factory function
- Document chunking via `DocumentProcessor`
- Similarity search for RAG (Retrieval Augmented Generation)

### Multi-LLM Support
- **Providers**: OpenAI, Claude (Anthropic), Gemini (Google), LLaMA (Ollama)
- Model pricing tracked in database (`input_token_rate_per_million`, `output_token_rate_per_million`)
- Model groups control user access to specific models
- Token usage tracking for billing

### Real-time Communication
- Django Channels for WebSocket support
- Real-time conversation streaming
- Connection validation and user authentication
- Graceful error handling and reconnection

## Database Design Patterns

### User & Access Control
- Custom User model extending `AbstractUser`
- Access code groups for registration control
- Model groups for LLM access permissions
- Platform-specific authentication (`auth_source`, scopes)

### Conversation System
- Hierarchical: User → Conversations → Messages → Snippets
- Conversation cloning with selective data copying
- Message feedback system (like/dislike with optional text)
- Token usage and cost tracking per message

### File Management
- File upload with background processing via Django RQ
- Tag system for organization
- Folder support for file grouping
- Processing status tracking (`PROCESSING`, `COMPLETED`, `FAILED`)
- Vector database source tracking

## Environment & Configuration

### Required Environment Variables
```bash
DJANGO_SETTINGS_MODULE=config.settings.local
DJANGO_DEBUG=True
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=localhost,127.0.0.1

# LLM API Keys
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...

# Vector Databases
PINECONE_API_KEY=...
WEAVIATE_URL=...

# Redis for background tasks
REDIS_HOST=localhost
REDIS_PORT=6379
```

### Settings Structure
- `config/settings/common.py`: Shared settings
- `config/settings/local.py`: Development settings
- `config/settings/production.py`: Production settings
- `config/env.py`: Environment variable management

## Testing Patterns

- Use Django's built-in TestCase
- Create test data using model factories
- Mock external services (OpenAI, vector DBs)
- Test permissions and user isolation
- Test WebSocket consumers with async test cases

## Code Style Conventions

- **Formatting**: Black (line length 88)
- **Import sorting**: isort
- **String wrapping**: Always wrap user-facing strings with `gettext_lazy as _`
- **Constants**: Define in `constants.py` files using classes with `TextChoices`
- **Documentation**: Comprehensive docstrings for services and complex methods
- **Error handling**: Never translate logged exceptions, only user-facing messages

## Development Workflow

1. **Feature branches**: `[Name]/[Feature/Fix/Refactor]/[Description]`
2. **Database changes**: Always create migrations
3. **API changes**: Update serializers and test endpoints
4. **New services**: Follow abstract base class patterns
5. **Background tasks**: Use Django RQ with proper error handling
6. **WebSocket changes**: Test real-time functionality thoroughly

## Common Patterns to Follow

### Model Method Pattern
```python
def clone(self, include_messages=True, **kwargs):
    """Clone with selective data copying."""
    with transaction.atomic():
        # Implementation
```

### Service Initialization Pattern
```python
def __init__(self):
    self.vector_service = get_vector_service()
    self.document_processor = DocumentProcessor()
```

### API Response Pattern
```python
return Response(
    {"detail": "Success message"},
    status=status.HTTP_200_OK
)
```

### Error Handling Pattern
```python
try:
    # Business logic
except SpecificException as e:
    logger.error(f"Operation failed: {e}")
    return Response(
        {"error": "User-friendly message"},
        status=status.HTTP_400_BAD_REQUEST
    )
```