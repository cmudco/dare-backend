"""
Simple Artifact Coordinator

Simplified artifact generation - streams LLM response directly to artifact panel.
No sections, no outlines, no checkpointing. Just like Claude's artifacts.
"""

import logging
import json
from typing import Optional, Dict, Any, Callable, Literal

from channels.db import database_sync_to_async
from djangorestframework_camel_case.util import camelize

from conversations.models import Conversation, Message, LLM, Artifact, ArtifactGroup
from conversations.constants import ArtifactType, ArtifactStatus
from core.services.llm_service import LLMService
from core.services.dtos import LLMQueryRequestBuilder
from core.services.billing_service import BillingService
from core.services.llm_utils.diagram_tool import (
    get_diagram_tool,
    json_to_mermaid,
)

logger = logging.getLogger(__name__)


class SimpleArtifactCoordinator:
    """
    Simplified artifact generation coordinator.
    
    Key principle: Uses the same LLM streaming as normal chat,
    but creates an artifact record and routes output to artifact panel.
    
    Differences from old LangGraph coordinator:
    - No sections or outlines
    - No checkpointing
    - No pause/resume
    - Direct streaming to artifact
    """
    
    def __init__(
        self,
        conversation: Conversation,
        user=None,
        send_callback: Optional[Callable] = None,
    ):
        """
        Initialize the simple artifact coordinator.
        
        Args:
            conversation: The conversation instance
            user: User object (None for public bots)
            send_callback: Async callback for sending WebSocket messages
        """
        self.conversation = conversation
        self.user = user
        self.send_callback = send_callback
        self.llm_service = LLMService()
        self.billing_service = BillingService()
    
    async def send(self, data: Dict[str, Any]):
        """Send data through WebSocket if callback is available."""
        if self.send_callback:
            try:
                # Preserve the 'type' field in snake_case (required for frontend action dispatch)
                # but camelize all other fields
                event_type = data.get("type", "")
                camelized = camelize(data)
                camelized["type"] = event_type  # Restore original snake_case type
                
                await self.send_callback(json.dumps(camelized))
            except Exception as e:
                logger.debug(f"Failed to send WebSocket message: {type(e).__name__}")
    
    async def stream_artifact_response(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
        intent: Literal["create", "edit"],
        active_artifact_id: Optional[int] = None,
    ):
        """
        Stream LLM response to artifact panel.
        
        Args:
            message_data: Message data (same as normal message flow)
            message_obj: AI message object (for linking)
            llm: LLM to use
            intent: "create" or "edit"
            active_artifact_id: ID of active artifact (for edit)
        """
        artifact_data = None
        previous_content = ""
        
        try:
            # 1. Create or get artifact
            if intent == "create":
                artifact = await self._create_artifact(message_data, message_obj)
                logger.info(f"Created new artifact id={artifact.id}")
            else:  # edit
                artifact, previous_content = await self._create_artifact_version(
                    active_artifact_id, message_data, message_obj
                )
                logger.info(f"Created artifact version id={artifact.id} (parent={active_artifact_id})")
            
            # 2. Send artifact_start event
            # 2. Send artifact_start event
            artifact_data = await self._get_artifact_data(artifact)
            start_event = {
                "type": "artifact_start",
                "artifactId": artifact_data["id"],
                "title": artifact_data["title"],
                "messageId": message_obj.id,
                "version": artifact_data["version"],
                "isNewVersion": intent == "edit",
                "parentArtifactId": artifact_data.get("parent_artifact_id"),
                "artifactGroupId": artifact_data.get("artifact_group_id"),
            }
            logger.info(f"Sending artifact_start: {start_event}")
            await self.send(start_event)
            
            # 3. Build request - for edit, tell LLM to generate ONLY the continuation
            message_for_llm = message_data["message"]
            if intent == "edit" and previous_content:
                # APPEND mode: LLM generates ONLY new content, we'll prepend existing
                message_for_llm = (
                    f"You are continuing/extending an existing document.\n\n"
                    f"EXISTING CONTENT (already written):\n"
                    f"---\n{previous_content}\n---\n\n"
                    f"USER REQUEST: {message_data['message']}\n\n"
                    f"IMPORTANT: Output ONLY the NEW content to be added. "
                    f"Do NOT repeat the existing content. Start directly with the continuation."
                )
                logger.info(f"Edit/append mode: {len(previous_content)} chars existing content")
            
            request = LLMQueryRequestBuilder.from_message_data(
                message=message_for_llm,
                conversation=self.conversation,
                user=self.user,
                message_data=message_data,
                llm=llm,
                message_obj=message_obj,
            )
            
            # 4. Stream from LLM service
            new_content = ""  # LLM-generated content (for edits, this is ONLY the new part)
            token_usage = None
            chunk_count = 0
            
            async for chunk, usage in self.llm_service.query(request):
                if usage:
                    token_usage = usage
                    
                    # Check billing during streaming
                    if self.user:
                        can_continue, error_response = await self.billing_service.check_streaming_credit_usage(
                            self.user, llm, usage
                        )
                        if not can_continue:
                            # Handle insufficient balance
                            final_content = previous_content + "\n\n" + new_content if intent == "edit" else new_content
                            await self._handle_insufficient_balance(
                                artifact, final_content, message_obj, token_usage
                            )
                            return
                
                if chunk and chunk.strip():
                    new_content += chunk
                    chunk_count += 1
                    
                    # For edit: show combined content (existing + new)
                    # For create: show just the new content
                    display_content = previous_content + "\n\n" + new_content if intent == "edit" else new_content
                    
                    # Send artifact_stream event
                    await self.send({
                        "type": "artifact_stream",
                        "artifactId": artifact_data["id"],
                        "content": display_content,
                        "streaming": True,
                    })
            
            logger.info(f"Streaming complete: {chunk_count} chunks, {len(new_content)} chars new content")
            
            # 5. Combine content for final save
            final_content = previous_content + "\n\n" + new_content if intent == "edit" else new_content
            logger.debug(f"Final content length: {len(final_content)} chars, intent={intent}")
            
            # 6. Finalize artifact
            await self._finalize_artifact(
                artifact, final_content, message_obj, token_usage
            )
            logger.debug(f"Finalized artifact id={artifact_data['id']}")
            
            # 7. Send artifact_complete event
            # NOTE: Content is NOT included - already streamed via artifact_stream events
            # Including large content here can cause Socket.IO issues with large payloads
            complete_event = {
                "type": "artifact_complete",
                "artifactId": artifact_data["id"],
                "wordCount": len(final_content.split()),
                "messageId": message_obj.id,
            }
            logger.info(f"Sending artifact_complete: artifactId={artifact_data['id']}, words={len(final_content.split())}")
            await self.send(complete_event)
            logger.debug(f"artifact_complete sent successfully for id={artifact_data['id']}")
            
            # 8. Send message completion event to stop streaming indicator
            message_event = {
                "type": "message",
                "id": message_obj.id,
                "message": f"[Artifact: {artifact_data['title']}]",
                "artifactId": str(artifact_data["id"]),
                "senderType": 2,  # AI
                "streaming": False,
            }
            logger.info(f"Sending message event: messageId={message_obj.id}, artifactId={artifact_data['id']}")
            await self.send(message_event)
            logger.debug(f"message event sent successfully for id={message_obj.id}")
            
        except Exception as e:
            logger.exception(f"Error streaming artifact: {e}")
            await self.send({
                "type": "artifact_error",
                "error": str(e),
                "artifactId": artifact_data["id"] if artifact_data else None,
            })
    
    async def stream_diagram_response(
        self,
        message_data: Dict[str, Any],
        message_obj: Message,
        llm: LLM,
    ):
        """
        Generate a diagram using tool calls and stream to artifact panel.
        
        Uses structured output via tool calls to get JSON, then converts to mermaid.
        This is non-streaming because tool calls return complete JSON at once.
        
        Args:
            message_data: Message data containing user's diagram request
            message_obj: AI message object (for linking)
            llm: LLM to use (must support function calling)
        """
        artifact_data = None
        
        try:
            # 1. Create artifact for diagram
            artifact = await self._create_diagram_artifact(message_data, message_obj)
            logger.info(f"Created diagram artifact id={artifact.id}")
            
            # 2. Send artifact_start event
            artifact_data = await self._get_artifact_data(artifact)
            start_event = {
                "type": "artifact_start",
                "artifactId": artifact_data["id"],
                "title": artifact_data["title"],
                "messageId": message_obj.id,
                "version": artifact_data["version"],
                "isNewVersion": False,
            }
            logger.info(f"Sending diagram artifact_start: {start_event}")
            await self.send(start_event)
            
            # 3. Send "generating" status update
            await self.send({
                "type": "artifact_stream",
                "artifactId": artifact_data["id"],
                "content": "_Generating diagram..._",
                "streaming": True,
            })
            
            # 4. Get diagram tool for the provider
            tool = get_diagram_tool(llm.provider)
            
            # 5. Build request with tool
            request = LLMQueryRequestBuilder.from_message_data(
                message=message_data["message"],
                conversation=self.conversation,
                user=self.user,
                message_data=message_data,
                llm=llm,
                message_obj=message_obj,
            )
            
            # 6. Call LLM with tool - collect full response (tool calls are non-streaming)
            full_response = ""
            token_usage = None
            tool_call_data = None
            
            logger.info(f"Calling LLM with diagram tool for provider {llm.provider}: {type(tool).__name__}")
            
            async for chunk, usage in self.llm_service.query(request, tools=[tool]):
                if usage:
                    token_usage = usage
                    # Check for tool call in usage
                    if usage.get("tool_calls"):
                        tool_call_data = usage["tool_calls"]
                        logger.info(f"Tool call received: {tool_call_data}")
                
                if chunk:
                    full_response += chunk
            
            # 7. Parse tool call response or use raw response
            mermaid_content = None
            
            if tool_call_data:
                # Tool call response - parse JSON and convert to mermaid
                try:
                    import json
                    # Handle different tool call formats
                    if isinstance(tool_call_data, list) and len(tool_call_data) > 0:
                        tc = tool_call_data[0]
                        if isinstance(tc, dict):
                            # Claude format: {'name': '...', 'arguments': '...'}
                            # OpenAI format: {'function': {'name': '...', 'arguments': '...'}}
                            if "arguments" in tc:
                                # Claude format - arguments directly on the tool call
                                args = tc["arguments"]
                            elif "function" in tc:
                                # OpenAI format - under function key
                                args = tc.get("function", {}).get("arguments", "{}")
                            else:
                                args = "{}"
                            
                            if isinstance(args, str):
                                diagram_json = json.loads(args)
                            else:
                                diagram_json = args
                        else:
                            # Object-like access (OpenAI SDK objects)
                            if hasattr(tc, 'function'):
                                diagram_json = json.loads(tc.function.arguments)
                            else:
                                diagram_json = {}
                    else:
                        diagram_json = tool_call_data
                    
                    mermaid_content = json_to_mermaid(diagram_json)
                    logger.info(f"Converted tool call to mermaid: {len(mermaid_content)} chars")
                    
                except Exception as e:
                    logger.exception(f"Error parsing tool call: {e}")
                    # Fall back to treating response as mermaid
                    mermaid_content = None
            
            # 8. If no tool call, check if LLM returned mermaid directly
            if not mermaid_content:
                # Try to extract mermaid from markdown code block
                if "```mermaid" in full_response:
                    import re
                    match = re.search(r'```mermaid\s*(.*?)\s*```', full_response, re.DOTALL)
                    if match:
                        mermaid_content = match.group(1).strip()
                        logger.info("Extracted mermaid from markdown code block")
                elif "flowchart" in full_response.lower() or "sequenceDiagram" in full_response:
                    # Likely raw mermaid
                    mermaid_content = full_response.strip()
                else:
                    # Last resort - use the raw response
                    mermaid_content = f"```\n{full_response}\n```"
                    logger.warning("Could not extract mermaid, using raw response")
            
            # 9. Wrap in mermaid code block for rendering
            final_content = f"```mermaid\n{mermaid_content}\n```"
            
            # 10. Send final diagram content
            await self.send({
                "type": "artifact_stream",
                "artifactId": artifact_data["id"],
                "content": final_content,
                "streaming": False,
            })
            
            # 11. Finalize artifact
            await self._finalize_artifact(
                artifact, final_content, message_obj, token_usage
            )
            
            # 12. Send artifact_complete
            complete_event = {
                "type": "artifact_complete",
                "artifactId": artifact_data["id"],
                "wordCount": len(mermaid_content.split()),
                "messageId": message_obj.id,
            }
            logger.info(f"Sending artifact_complete for diagram: {artifact_data['id']}")
            await self.send(complete_event)
            
            # 13. Send message completion
            message_event = {
                "type": "message",
                "id": message_obj.id,
                "message": f"[Diagram: {artifact_data['title']}]",
                "artifactId": str(artifact_data["id"]),
                "senderType": 2,  # AI
                "streaming": False,
            }
            await self.send(message_event)
            
        except Exception as e:
            logger.exception(f"Error generating diagram: {e}")
            await self.send({
                "type": "artifact_error",
                "error": str(e),
                "artifactId": artifact_data["id"] if artifact_data else None,
            })
    
    async def _create_diagram_artifact(
        self,
        message_data: Dict,
        message_obj: Message
    ) -> Artifact:
        """Create artifact for diagram with DIAGRAM type."""
        title = self._extract_title(message_data["message"])
        if not title.lower().startswith(("diagram", "flowchart", "chart")):
            title = f"Diagram: {title}"
        
        def _create():
            artifact = Artifact(
                conversation=self.conversation,
                message=message_obj,
                title=title,
                artifact_type=ArtifactType.DIAGRAM,
                status=ArtifactStatus.GENERATING,
                version=1,
                estimated_sections=1,
                current_section=0,
            )
            artifact.save()
            
            # Create artifact group
            group = ArtifactGroup(
                conversation=self.conversation,
                base_title=title,
                latest_version=artifact,
            )
            group.save()
            
            artifact.artifact_group = group
            artifact.save(update_fields=["artifact_group"])
            
            return artifact
        
        return await database_sync_to_async(_create)()
    
    async def _create_artifact(
        self, 
        message_data: Dict, 
        message_obj: Message
    ) -> Artifact:
        """
        Create new artifact with placeholder title.
        
        Args:
            message_data: Message data containing user message
            message_obj: AI message object to link
            
        Returns:
            Created Artifact instance
        """
        title = self._extract_title(message_data["message"])
        
        def _create():
            # Create artifact using constructor (model uses active_objects, not objects)
            artifact = Artifact(
                conversation=self.conversation,
                message=message_obj,
                title=title,
                artifact_type=ArtifactType.DOCUMENT,
                status=ArtifactStatus.GENERATING,
                version=1,
                estimated_sections=1,  # Simplified - no sections
                current_section=0,
            )
            artifact.save()
            
            # Create artifact group
            group = ArtifactGroup(
                conversation=self.conversation,
                base_title=title,
                latest_version=artifact,
            )
            group.save()
            
            artifact.artifact_group = group
            artifact.save(update_fields=["artifact_group"])
            
            return artifact
        
        return await database_sync_to_async(_create)()
    
    async def _create_artifact_version(
        self, 
        parent_id: int, 
        message_data: Dict,
        message_obj: Message,
    ) -> tuple:
        """
        Create new version of existing artifact.
        
        Args:
            parent_id: ID of parent artifact
            message_data: Message data
            message_obj: AI message object to link
            
        Returns:
            Tuple of (Created Artifact instance, previous content string)
        """
        def _create_version():
            parent = Artifact.active_objects.select_related('artifact_group').get(id=parent_id)
            previous_content = parent.content or ""
            
            # Create new version using existing model method
            new_artifact = parent.create_new_version()
            new_artifact.message = message_obj
            new_artifact.status = ArtifactStatus.GENERATING
            new_artifact.save(update_fields=["message", "status"])
            
            return new_artifact, previous_content
        
        return await database_sync_to_async(_create_version)()
    
    async def _get_artifact_data(self, artifact: Artifact) -> Dict:
        """Get artifact data for WebSocket events."""
        def _get():
            return {
                "id": artifact.id,
                "title": artifact.title,
                "version": artifact.version,
                "parent_artifact_id": artifact.parent_artifact_id,
                "artifact_group_id": artifact.artifact_group_id,
            }
        return await database_sync_to_async(_get)()
    
    async def _finalize_artifact(
        self,
        artifact: Artifact,
        content: str,
        message_obj: Message,
        token_usage: Optional[Dict],
    ):
        """
        Finalize artifact with content and update message.
        
        Args:
            artifact: Artifact to finalize
            content: Generated content
            message_obj: AI message to update
            token_usage: Token usage data
        """
        def _save():
            # Update artifact
            artifact.content = content
            artifact.status = ArtifactStatus.COMPLETED
            artifact.current_section = 1  # Simplified
            artifact.estimated_sections = 1
            
            # Extract better title from content if current title is placeholder
            if artifact.title.startswith("New ") or len(artifact.title) > 100:
                first_line = content.split('\n')[0].strip()
                if first_line.startswith('#'):
                    artifact.title = first_line.lstrip('#').strip()[:100]
                elif len(first_line) < 100:
                    artifact.title = first_line[:100]
            
            artifact.save()
            
            # Update message to reference artifact
            message_obj.message = f"[Artifact: {artifact.title}]"
            
            # Add token usage if available
            if token_usage:
                message_obj.input_tokens = token_usage.get("input_tokens") or token_usage.get("prompt_tokens")
                message_obj.output_tokens = token_usage.get("output_tokens") or token_usage.get("completion_tokens")
            
            message_obj.save()
        
        await database_sync_to_async(_save)()
        
        # Handle billing
        if self.user and token_usage:
            await database_sync_to_async(
                self.billing_service.finalize_ai_message
            )(message_obj, f"[Artifact: {artifact.title}]", token_usage)
    
    async def _handle_insufficient_balance(
        self,
        artifact: Artifact,
        content: str,
        message_obj: Message,
        token_usage: Dict,
    ):
        """Handle insufficient balance during streaming."""
        # Save partial content
        await self._finalize_artifact(artifact, content, message_obj, token_usage)
        
        # Update status to indicate partial
        def _update_status():
            artifact.status = ArtifactStatus.ERROR
            artifact.metadata = artifact.metadata or {}
            artifact.metadata["error"] = "Insufficient balance - partial content saved"
            artifact.save()
        
        await database_sync_to_async(_update_status)()
        
        # Send error event
        await self.send({
            "type": "artifact_error",
            "artifactId": artifact.id,
            "error": "Insufficient balance to continue generation",
            "partialContent": True,
        })
    
    def _extract_title(self, message: str) -> str:
        """
        Extract title from user message.
        
        Args:
            message: User's message
            
        Returns:
            Extracted title string
        """
        # Clean up message
        message = message.strip()
        
        # If short enough, use as-is
        if len(message) <= 50:
            return message
        
        # Use first few words
        words = message.split()[:8]
        title = " ".join(words)
        
        if len(message) > len(title):
            title = f"New: {title[:45]}..."
        
        return title
