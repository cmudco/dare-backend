"""
Unified executor for all artifact-creating tools.
Routes visual outputs to artifact panel instead of inline rendering.
"""
import json
import logging
from typing import Dict, Any, Callable, Optional

from asgiref.sync import sync_to_async

from conversations.models import Artifact, Message, Conversation, ArtifactGroup
from conversations.constants import ArtifactStatus, ArtifactType, ARTIFACT_CONTENT_TYPES
from core.services.llm_utils.diagram_tool import json_to_mermaid
from dare_tools.services.registry import execute_create_docx

logger = logging.getLogger(__name__)


class ArtifactToolExecutor:
    """
    Handles: create_chart, create_diagram, create_docx, create_react_component, update_artifact
    All tools create Artifact records and emit artifact_created/updated events.
    """

    SUPPORTED_TOOLS = ['create_chart', 'create_diagram', 'create_docx', 'update_artifact', 'update_artifact_inline', 'create_react_component']
    
    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        Execute tool and create artifact.
        
        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments from LLM
            message: The AI message associated with this tool call
            conversation: The conversation context
            send_callback: Callback to send WebSocket events
            
        Returns:
            Dict with success status and artifact_id
        """
        if tool_name == 'create_chart':
            return await self._execute_create_chart(arguments, message, conversation, send_callback)
        elif tool_name == 'create_diagram':
            return await self._execute_create_diagram(arguments, message, conversation, send_callback)
        elif tool_name == 'create_docx':
            return await self._execute_create_docx(arguments, message, conversation, send_callback)
        elif tool_name == 'create_react_component':
            return await self._execute_create_react_component(arguments, message, conversation, send_callback)
        elif tool_name == 'update_artifact':
            return await self._execute_update_artifact(arguments, message, conversation, send_callback)
        elif tool_name == 'update_artifact_inline':
            return await self._execute_update_artifact_inline(arguments, message, conversation, send_callback)
        else:
            raise ValueError(f"Unsupported tool: {tool_name}")
    
    async def _execute_create_chart(
        self,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        create_chart → Artifact with content_type='application/vnd.dare.chart+json'
        
        LLM sends flat arguments: chart_type, title, data, options
        We build the complete chart_config including Recharts-required fields.
        """
        # Extract from LLM arguments
        chart_type = arguments.get('chart_type', 'bar')
        title = arguments.get('title', 'Chart')
        data = arguments.get('data', [])
        options = arguments.get('options', {})
        # LLM now provides these directly - use sensible defaults for backwards compat
        data_keys = arguments.get('dataKeys', ['value'])
        x_axis_key = arguments.get('xAxisKey', 'label')
        
        # Build complete chart config with Recharts-required fields
        chart_config = {
            'type': chart_type,
            'title': title,
            'data': data,
            'dataKeys': data_keys,
            'xAxisKey': x_axis_key,
            'options': options,
        }
        
        # Sanitize title for filename
        safe_title = self._sanitize_filename(title)
        filename = f"{safe_title}.json"
        content = json.dumps(chart_config, indent=2)
        
        # Create artifact group and artifact
        artifact = await self._create_artifact(
            conversation=conversation,
            message=message,
            title=title,
            content=content,
            artifact_type=ArtifactType.CHART,
            filename=filename,
            content_type=ARTIFACT_CONTENT_TYPES['chart'],
            source_tool='create_chart',
            metadata={'chartType': chart_type},
        )
        
        # Emit artifact_created event
        await self._emit_artifact_created(
            send_callback=send_callback,
            artifact=artifact,
            message=message,
        )
        
        logger.info(f"Created chart artifact {artifact.id}: {title}")
        
        # Return full result with chart_config so LLM has context for modifications
        return {
            'success': True,
            'artifact_id': artifact.id,
            'message': f'Created chart: {title}',
            'chart_config': chart_config,
        }
    
    async def _execute_create_diagram(
        self,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        create_diagram → Artifact with content_type='text/mermaid'

        Expected arguments (from LLM structured output):
            - diagram_type: Enum value from LLM (flowchart, sequence, mindmap, etc.)
            - title: Optional diagram title
            - nodes: List of node definitions with id, label, shape
            - edges: List of edge definitions with from, to, label

        The nodes/edges are converted to mermaid syntax using json_to_mermaid().
        """
        title = arguments.get('title', 'Diagram')
        diagram_type = arguments.get('diagram_type', 'flowchart')

        # Convert structured nodes/edges to mermaid code
        # The LLM provides nodes/edges, we convert to mermaid syntax
        mermaid_code = json_to_mermaid(arguments)
        
        # Sanitize title for filename
        safe_title = self._sanitize_filename(title)
        filename = f"{safe_title}.mmd"
        
        # Create artifact
        artifact = await self._create_artifact(
            conversation=conversation,
            message=message,
            title=title,
            content=mermaid_code,
            artifact_type=ArtifactType.DIAGRAM,
            filename=filename,
            content_type=ARTIFACT_CONTENT_TYPES['diagram'],
            source_tool='create_diagram',
            metadata={'diagramType': diagram_type},
        )
        
        # Emit artifact_created event
        await self._emit_artifact_created(
            send_callback=send_callback,
            artifact=artifact,
            message=message,
        )
        
        logger.info(f"Created diagram artifact {artifact.id}: {title} (type: {diagram_type})")

        # Return full result with mermaid_code so LLM has context for modifications
        return {
            'success': True,
            'artifact_id': artifact.id,
            'message': f'Created diagram: {title}',
            'mermaid_code': mermaid_code,  # Include full code for LLM context
            'diagram_type': diagram_type,
        }

    async def _execute_create_docx(
        self,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        create_docx → Artifact with content_type='application/vnd.dare.docx+json'

        Expected arguments:
            - title: Document title
            - blocks: Ordered list of content blocks
        """
        validation_result = execute_create_docx(arguments)
        if not validation_result.get('success'):
            return validation_result

        doc_config = validation_result.get('doc_config', {})
        title = doc_config.get('title', 'Document')
        blocks = doc_config.get('blocks', [])

        safe_title = self._sanitize_filename(title)
        filename = f"{safe_title}.docx"
        content = json.dumps(doc_config, indent=2)

        artifact = await self._create_artifact(
            conversation=conversation,
            message=message,
            title=title,
            content=content,
            artifact_type=ArtifactType.DOCX,
            filename=filename,
            content_type=ARTIFACT_CONTENT_TYPES['docx'],
            source_tool='create_docx',
            metadata={'blockCount': len(blocks)},
        )

        await self._emit_artifact_created(
            send_callback=send_callback,
            artifact=artifact,
            message=message,
        )

        logger.info(f"Created docx artifact {artifact.id}: {title}")

        return {
            'success': True,
            'artifact_id': artifact.id,
            'message': f'Created document: {title}',
            'doc_config': doc_config,
        }

    async def _execute_create_react_component(
        self,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        create_react_component → Artifact with content_type='application/vnd.dare.react+jsx'

        Creates a React component artifact that will be rendered in a sandboxed iframe
        on the frontend with React 18, Tailwind CSS, Shadcn UI, and Recharts pre-loaded.

        Expected arguments (from LLM structured output):
            - title: Component title/name
            - code: Complete React component code (JSX)
            - description: Optional description of component functionality
        """
        title = arguments.get('title', 'React Component')
        code = arguments.get('code', '')
        description = arguments.get('description', '')

        # Validate code is not empty
        if not code.strip():
            return {
                'success': False,
                'error': 'Component code is required',
            }

        # Basic validation - check for component pattern
        if not self._is_valid_react_component(code):
            return {
                'success': False,
                'error': (
                    'Invalid React component. Must export a default function component '
                    'or define an App function. Example: export default function App() { ... }'
                ),
            }

        # Sanitize title for filename
        safe_title = self._sanitize_filename(title)
        filename = f"{safe_title}.jsx"

        # Create artifact
        artifact = await self._create_artifact(
            conversation=conversation,
            message=message,
            title=title,
            content=code,
            artifact_type=ArtifactType.REACT,
            filename=filename,
            content_type=ARTIFACT_CONTENT_TYPES['react'],
            source_tool='create_react_component',
            metadata={
                'description': description,
                'framework': 'react',
                'version': '18',
                'ui_library': 'shadcn',
            },
        )

        # Emit artifact_created event
        await self._emit_artifact_created(
            send_callback=send_callback,
            artifact=artifact,
            message=message,
        )

        logger.info(f"Created React component artifact {artifact.id}: {title}")

        return {
            'success': True,
            'artifact_id': artifact.id,
            'message': f'Created React component: {title}',
        }

    def _is_valid_react_component(self, code: str) -> bool:
        """
        Basic validation that code looks like a React component.
        Not a full parser, just catches obvious mistakes.
        """
        code_stripped = code.strip()

        # Check for common component patterns
        patterns = [
            'export default function',
            'export default ',
            'function App',
            'const App =',
            'let App =',
        ]

        return any(pattern in code_stripped for pattern in patterns)

    @sync_to_async
    def _create_artifact(
        self,
        conversation: Conversation,
        message: Message,
        title: str,
        content: str,
        artifact_type: str,
        filename: str,
        content_type: str,
        source_tool: str,
        metadata: Dict[str, Any] = None,
    ) -> Artifact:
        """Create artifact and artifact group in database."""
        # Create artifact group
        group = ArtifactGroup.active_objects.create(
            conversation=conversation,
            base_title=title,
        )
        
        # Create artifact using active_objects pattern
        artifact = Artifact.active_objects.create(
            conversation=conversation,
            message=message,
            artifact_group=group,
            title=title,
            content=content,
            artifact_type=artifact_type,
            filename=filename,
            content_type=content_type,
            source_tool=source_tool,
            status=ArtifactStatus.COMPLETED,
            metadata=metadata or {},
            version=1,
        )
        
        # Update group's latest version
        group.latest_version = artifact
        group.save(update_fields=['latest_version'])
        
        return artifact
    
    async def _emit_artifact_created(
        self,
        send_callback: Callable,
        artifact: Artifact,
        message: Message,
    ):
        """Emit artifact_created WebSocket event."""
        event_data = {
            'type': 'artifact_created',
            'artifactId': artifact.id,
            'messageId': message.id if message else None,
            'artifactGroupId': artifact.artifact_group_id,
            'filename': artifact.filename,
            'title': artifact.title,
            'contentType': artifact.content_type,
            'content': artifact.content,
            'artifactType': artifact.artifact_type,
            'version': artifact.version,
            'metadata': artifact.metadata,
        }
        
        # Handle async or sync callback
        if callable(send_callback):
            result = send_callback(event_data)
            if hasattr(result, '__await__'):
                await result
    
    async def _execute_update_artifact(
        self,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        update_artifact → Creates a new version of an existing artifact.
        
        Expected arguments:
            - artifact_id: ID of the artifact to update
            - content: New content for the artifact
            - title: Optional new title (defaults to original)
        """
        artifact_id = arguments.get('artifact_id')
        new_content = arguments.get('content', '')
        new_title = arguments.get('title')
        
        if not artifact_id:
            return {'success': False, 'error': 'artifact_id is required'}
        
        try:
            # Get the parent artifact and create new version
            new_artifact = await self._update_artifact_version(
                artifact_id=artifact_id,
                new_content=new_content,
                new_title=new_title,
                message=message,
            )
            
            # Emit artifact_updated event
            await self._emit_artifact_updated(
                send_callback=send_callback,
                artifact=new_artifact,
                parent_artifact_id=artifact_id,
                message=message,
            )
            
            logger.info(
                f"Updated artifact {artifact_id} -> new version {new_artifact.id} "
                f"(v{new_artifact.version})"
            )
            return {
                'success': True,
                'artifact_id': new_artifact.id,
                'version': new_artifact.version,
                'message': f'Updated artifact: {new_artifact.title} (v{new_artifact.version})',
            }
            
        except Artifact.DoesNotExist:
            return {'success': False, 'error': f'Artifact {artifact_id} not found'}
        except Exception as e:
            logger.exception(f"Error updating artifact {artifact_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    @sync_to_async
    def _update_artifact_version(
        self,
        artifact_id: int,
        new_content: str,
        new_title: Optional[str],
        message: Message,
    ) -> Artifact:
        """
        Create a new version of an artifact with updated content.
        Uses the existing create_new_version() method from the Artifact model.
        """
        # Get the parent artifact
        parent_artifact = Artifact.active_objects.get(id=artifact_id)
        
        # Create new version using the model's method
        new_artifact = parent_artifact.create_new_version()
        
        # Update with new content
        new_artifact.content = new_content
        new_artifact.message = message
        new_artifact.status = ArtifactStatus.COMPLETED
        new_artifact.source_tool = 'update_artifact'
        
        if new_title:
            new_artifact.title = new_title
        
        new_artifact.save(update_fields=[
            'content', 'message', 'status', 'source_tool', 'title', 'updated_at'
        ])
        
        return new_artifact
    
    async def _emit_artifact_updated(
        self,
        send_callback: Callable,
        artifact: Artifact,
        parent_artifact_id: int,
        message: Message,
    ):
        """Emit artifact_updated WebSocket event."""
        event_data = {
            'type': 'artifact_updated',
            'artifactId': artifact.id,
            'parentArtifactId': parent_artifact_id,
            'artifactGroupId': artifact.artifact_group_id,
            'messageId': message.id if message else None,
            'filename': artifact.filename,
            'title': artifact.title,
            'contentType': artifact.content_type,
            'content': artifact.content,
            'artifactType': artifact.artifact_type,
            'version': artifact.version,
            'metadata': artifact.metadata,
        }
        
        # Handle async or sync callback
        if callable(send_callback):
            result = send_callback(event_data)
            if hasattr(result, '__await__'):
                await result
    
    def _sanitize_filename(self, title: str) -> str:
        """Convert title to safe filename."""
        # Replace spaces with underscores, remove special chars
        safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in title.lower())
        # Remove consecutive underscores
        while '__' in safe:
            safe = safe.replace('__', '_')
        return safe.strip('_') or 'artifact'

    async def _execute_update_artifact_inline(
        self,
        arguments: Dict[str, Any],
        message: Message,
        conversation: Conversation,
        send_callback: Callable,
    ) -> Dict[str, Any]:
        """
        update_artifact_inline -> Make targeted string replacement in an artifact.

        Uses exact string matching (like Claude's str_replace) for small edits.
        Creates a new version with the replaced content.

        IMPORTANT: Requires old_str to be UNIQUE in the artifact to avoid ambiguity.

        Expected arguments:
            - artifact_id: ID of the artifact to modify
            - old_str: Exact string to find (must be unique)
            - new_str: Replacement string
        """
        artifact_id = arguments.get('artifact_id')
        old_str = arguments.get('old_str', '')
        new_str = arguments.get('new_str', '')

        # Debug log what the LLM actually sent
        logger.debug(
            f"[update_artifact_inline] artifact_id={artifact_id}, "
            f"old_str={repr(old_str[:100])}{'...' if len(old_str) > 100 else ''}, "
            f"new_str={repr(new_str[:100])}{'...' if len(new_str) > 100 else ''}"
        )

        if not artifact_id:
            return {'success': False, 'error': 'artifact_id is required'}

        if not old_str:
            return {'success': False, 'error': 'old_str is required and cannot be empty'}

        try:
            # Get the artifact - but always use the LATEST version from its group
            # This ensures edits apply to current state, not stale versions
            artifact = await self._get_latest_artifact_version(artifact_id)

            if artifact is None:
                return {'success': False, 'error': f'Artifact {artifact_id} not found'}

            # Log which artifact we're actually editing
            if artifact.id != artifact_id:
                logger.info(
                    f"[update_artifact_inline] Redirecting from artifact #{artifact_id} "
                    f"to latest version #{artifact.id} (v{artifact.version})"
                )

            # Check for uniqueness - critical for avoiding ambiguous edits
            occurrence_count = artifact.content.count(old_str)

            if occurrence_count == 0:
                return {
                    'success': False,
                    'error': (
                        f'String not found in artifact #{artifact_id}. '
                        'The old_str must match exactly, including whitespace and newlines.'
                    )
                }

            if occurrence_count > 1:
                return {
                    'success': False,
                    'error': (
                        f'String appears {occurrence_count} times in artifact #{artifact_id}. '
                        'Please provide a more unique string that includes surrounding context.'
                    )
                }

            # Now safe to replace (exactly one occurrence)
            new_content = artifact.content.replace(old_str, new_str, 1)

            # Create new version with updated content
            # Use artifact.id (the actual latest version) not artifact_id (what LLM passed)
            new_artifact = await self._update_artifact_version(
                artifact_id=artifact.id,
                new_content=new_content,
                new_title=None,  # Keep original title
                message=message,
            )

            # Emit artifact_updated event with inline update type
            await self._emit_artifact_updated_inline(
                send_callback=send_callback,
                artifact=new_artifact,
                parent_artifact_id=artifact.id,
                message=message,
            )

            logger.info(
                f"Inline updated artifact {artifact.id} -> new version {new_artifact.id} "
                f"(v{new_artifact.version}), replaced {len(old_str)} chars with {len(new_str)} chars. "
                f"old_str preview: {repr(old_str[:50])}..., new_str preview: {repr(new_str[:50])}..."
            )

            return {
                'success': True,
                'artifact_id': new_artifact.id,
                'version': new_artifact.version,
                'message': f'Updated artifact: {new_artifact.title} (v{new_artifact.version})',
                'change_summary': {
                    'removed_chars': len(old_str),
                    'added_chars': len(new_str),
                    'net_change': len(new_str) - len(old_str),
                }
            }

        except Artifact.DoesNotExist:
            return {'success': False, 'error': f'Artifact {artifact_id} not found'}
        except Exception as e:
            logger.exception(f"Error in update_artifact_inline for {artifact_id}: {e}")
            return {'success': False, 'error': str(e)}

    @sync_to_async
    def _get_artifact_by_id(self, artifact_id: int) -> Optional[Artifact]:
        """Fetch an artifact by ID.

        Args:
            artifact_id: The artifact's database ID

        Returns:
            Artifact instance or None if not found
        """
        try:
            return Artifact.active_objects.get(id=artifact_id)
        except Artifact.DoesNotExist:
            return None

    @sync_to_async
    def _get_latest_artifact_version(self, artifact_id: int) -> Optional[Artifact]:
        """Fetch the LATEST version of an artifact from its group.

        When the LLM references an artifact by ID, it might reference an older version.
        This method finds the artifact group and returns its latest_version to ensure
        edits are applied to the current state.

        Args:
            artifact_id: The artifact's database ID (may be any version in the group)

        Returns:
            The latest Artifact version from the group, or None if not found
        """
        try:
            artifact = Artifact.active_objects.select_related(
                'artifact_group__latest_version'
            ).get(id=artifact_id)

            # If the artifact has a group with a latest_version, use that
            if artifact.artifact_group and artifact.artifact_group.latest_version:
                return artifact.artifact_group.latest_version

            # Fallback to the requested artifact if no group/latest
            return artifact
        except Artifact.DoesNotExist:
            return None

    async def _emit_artifact_updated_inline(
        self,
        send_callback: Callable,
        artifact: Artifact,
        parent_artifact_id: int,
        message: Message,
    ):
        """Emit artifact_updated WebSocket event with inline update type.

        Includes updateType='inline' to allow frontend to differentiate
        from full rewrites and potentially show diff animations.
        """
        event_data = {
            'type': 'artifact_updated',
            'updateType': 'inline',  # Distinguishes from full rewrite
            'artifactId': artifact.id,
            'parentArtifactId': parent_artifact_id,
            'artifactGroupId': artifact.artifact_group_id,
            'messageId': message.id if message else None,
            'filename': artifact.filename,
            'title': artifact.title,
            'contentType': artifact.content_type,
            'content': artifact.content,
            'artifactType': artifact.artifact_type,
            'version': artifact.version,
            'metadata': artifact.metadata,
        }

        # Handle async or sync callback
        if callable(send_callback):
            result = send_callback(event_data)
            if hasattr(result, '__await__'):
                await result


# Global executor instance
artifact_tool_executor = ArtifactToolExecutor()
