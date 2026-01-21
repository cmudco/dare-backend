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

logger = logging.getLogger(__name__)


class ArtifactToolExecutor:
    """
    Handles: create_chart, create_diagram
    All tools create Artifact records and emit artifact_created events.
    """
    
    SUPPORTED_TOOLS = ['create_chart', 'create_diagram']
    
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
        
        Expected arguments:
            - chart_config: Dict containing Recharts configuration
            - title: Optional chart title
        """
        chart_config = arguments.get('chart_config', {})
        title = arguments.get('title', 'Chart')
        
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
            metadata={'chartType': chart_config.get('type', 'bar')},
        )
        
        # Emit artifact_created event
        await self._emit_artifact_created(
            send_callback=send_callback,
            artifact=artifact,
            message=message,
        )
        
        logger.info(f"Created chart artifact {artifact.id}: {title}")
        return {'success': True, 'artifact_id': artifact.id, 'message': f'Created chart: {title}'}
    
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
            - mermaid_code: String containing Mermaid diagram code
            - diagram_type: Enum value from LLM (flowchart, sequence, mindmap, etc.)
            - title: Optional diagram title
        """
        mermaid_code = arguments.get('mermaid_code', '')
        title = arguments.get('title', 'Diagram')
        # diagram_type comes directly from LLM as structured output enum
        diagram_type = arguments.get('diagram_type', 'flowchart')
        
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
        return {'success': True, 'artifact_id': artifact.id, 'message': f'Created diagram: {title}'}
    
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
        group = ArtifactGroup.objects.create(
            conversation=conversation,
            base_title=title,
        )
        
        # Create artifact using active_objects pattern
        artifact = Artifact.objects.create(
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
            'filename': artifact.filename,
            'title': artifact.title,
            'contentType': artifact.content_type,
            'content': artifact.content,
            'artifactType': artifact.artifact_type,
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


# Global executor instance
artifact_tool_executor = ArtifactToolExecutor()

