"""
MemU Service Wrapper

Provides a service layer for cross-conversation memory using memu-py.
Uses SQLite for local development and pgvector for production.
"""
import json
import logging
import os
import tempfile
from typing import Any, Optional

from django.conf import settings

logger = logging.getLogger(__name__)

# Singleton instance
_memu_service: Optional["MemUService"] = None


class MemUService:
    """
    Wrapper for memu-py MemoryService.
    
    Handles initialization with appropriate database config based on environment
    and provides simplified async methods for memory operations.
    """

    def __init__(self):
        self._service = None
        self._initialized = False

    async def _ensure_initialized(self):
        """Lazy initialization of the MemoryService."""
        if self._initialized:
            return

        try:
            from memu.app import (
                MemoryService,
                LLMConfig,
                LLMProfilesConfig,
                DatabaseConfig,
                RetrieveConfig,
            )

            # Get OpenAI API key from environment
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                raise ValueError("OPENAI_API_KEY environment variable not set")

            # Configure LLM profiles for memory extraction and embeddings
            llm_profiles = LLMProfilesConfig(
                root={
                    "default": LLMConfig(
                        provider="openai",
                        api_key=openai_api_key,
                        chat_model="gpt-4o-mini",
                        embed_model="text-embedding-3-small",
                    )
                }
            )

            # Use SQLite for local, pgvector for production
            is_local = getattr(settings, "DEBUG", True)
            if is_local:
                # SQLite database path for local development
                db_path = os.path.join(settings.BASE_DIR, "memu_local.db")
                database_config = DatabaseConfig(
                    provider="sqlite",
                    path=db_path,
                )
                logger.info(f"MemU initialized with SQLite at: {db_path}")
            else:
                # pgvector for production - uses DATABASE_URL from environment
                database_config = DatabaseConfig()
                logger.info("MemU initialized with pgvector")

            # Configure retrieval with RAG method
            retrieve_config = RetrieveConfig(
                method="rag",
                route_intention=False,  # Always retrieve, don't route
            )

            self._service = MemoryService(
                llm_profiles=llm_profiles,
                database_config=database_config,
                retrieve_config=retrieve_config,
            )
            self._initialized = True
            logger.info("MemU service initialized successfully")

        except ImportError as e:
            logger.error(f"Failed to import memu-py: {e}")
            raise ImportError(
                "memu-py is not installed. Run: pip install memu-py"
            ) from e
        except Exception as e:
            logger.error(f"Failed to initialize MemU service: {e}")
            raise

    async def list_items(self, user_id: str) -> list[dict[str, Any]]:
        """
        List all memory items for a user.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            List of memory item dictionaries
        """
        await self._ensure_initialized()
        
        try:
            # List all items from memu
            result = await self._service.list_memory_items()
            
            # Debug logging
            logger.info(f"list_memory_items returned type: {type(result)}")
            logger.info(f"list_memory_items returned: {result}")
            
            # Handle dict response format {'items': [...]}
            if isinstance(result, dict):
                items_list = result.get("items", [])
            elif isinstance(result, (list, tuple)):
                items_list = result
            else:
                logger.warning(f"Unexpected return type from list_memory_items: {type(result)}")
                return []
            
            if not items_list:
                return []
            
            logger.info(f"Total items in DB: {len(items_list)}")
            
            # Convert items to dicts if they're objects
            user_items = []
            for i, item in enumerate(items_list):
                logger.info(f"Item {i} type: {type(item)}, value: {item}")
                
                # Handle both dict and object formats
                if hasattr(item, "model_dump"):
                    item_dict = item.model_dump()
                elif isinstance(item, dict):
                    item_dict = item
                elif hasattr(item, "__dict__") and not isinstance(item, str):
                    item_dict = vars(item)
                else:
                    # Try to convert to dict representation
                    item_dict = {"id": str(i), "content": str(item), "memoryType": "unknown", "categories": []}
                
                # Add to results
                user_items.append(item_dict)
            
            logger.info(f"Returning {len(user_items)} items for user {user_id}")
            return user_items
        except Exception as e:
            logger.error(f"Failed to list memory items for user {user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    async def search(self, user_id: str, query: str) -> dict[str, Any]:
        """
        Perform vector search on user's memories.
        
        Args:
            user_id: The user's unique identifier
            query: The search query
            
        Returns:
            Dict with categories, items, and resources
        """
        await self._ensure_initialized()
        
        try:
            result = await self._service.retrieve(
                queries=[query],
                where={"user_id": user_id}
            )
            return result if result else {"categories": [], "items": [], "resources": []}
        except Exception as e:
            logger.error(f"Failed to search memories for user {user_id}: {e}")
            raise

    async def get_item(self, item_id: str) -> Optional[dict[str, Any]]:
        """
        Get a single memory item by ID.
        
        Args:
            item_id: The memory item's unique identifier
            
        Returns:
            Memory item dict or None if not found
        """
        await self._ensure_initialized()
        
        try:
            result = await self._service.get_memory_item(memory_id=item_id)
            return result
        except Exception as e:
            logger.error(f"Failed to get memory item {item_id}: {e}")
            raise

    async def delete_item(self, item_id: str) -> bool:
        """
        Delete a memory item.
        
        Args:
            item_id: The memory item's unique identifier
            
        Returns:
            True if deleted successfully
        """
        await self._ensure_initialized()
        
        try:
            await self._service.delete_memory_item(memory_id=item_id)
            return True
        except Exception as e:
            logger.error(f"Failed to delete memory item {item_id}: {e}")
            raise

    async def clear_all(self, user_id: str) -> bool:
        """
        Clear all memory items for a user.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            True if cleared successfully
        """
        await self._ensure_initialized()
        
        try:
            await self._service.clear_memory(where={"user_id": user_id})
            return True
        except Exception as e:
            logger.error(f"Failed to clear memories for user {user_id}: {e}")
            raise

    async def create_item(
        self,
        user_id: str,
        memory_type: str,
        content: str,
        categories: list[str],
    ) -> dict[str, Any]:
        """
        Create a memory item manually.
        
        Args:
            user_id: The user's unique identifier
            memory_type: Type of memory (profile, event, knowledge, behavior)
            content: The memory content
            categories: List of category names
            
        Returns:
            Created memory item dict
        """
        await self._ensure_initialized()
        
        try:
            result = await self._service.create_memory_item(
                memory_type=memory_type,
                memory_content=content,
                memory_categories=categories,
                user={"id": user_id},
            )
            return result
        except Exception as e:
            logger.error(f"Failed to create memory item for user {user_id}: {e}")
            raise

    async def memorize_conversation(
        self, user_id: str, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """
        Extract and store memories from a conversation.
        
        Args:
            user_id: The user's unique identifier
            messages: List of message dicts with 'role' and 'content'
            
        Returns:
            Dict with extracted items, categories, and resource reference
        """
        await self._ensure_initialized()
        
        try:
            # Write messages to temp file (memu reads from file/URL)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(messages, f)
                conv_path = f.name
            
            logger.info(f"[SEED DEBUG] Calling memorize with file: {conv_path}")
            logger.info(f"[SEED DEBUG] Messages: {messages}")
            logger.info(f"[SEED DEBUG] User: {user_id}")

            result = await self._service.memorize(
                resource_url=conv_path,
                modality="conversation",
                user={"id": user_id},
            )
            
            logger.info(f"[SEED DEBUG] memorize returned type: {type(result)}")
            logger.info(f"[SEED DEBUG] memorize returned: {result}")

            # Cleanup temp file
            os.unlink(conv_path)

            return result if result else {"items": [], "categories": [], "resource": None}
        except Exception as e:
            logger.error(f"Failed to memorize conversation for user {user_id}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    async def seed_demo_data(self, user_id: str) -> dict[str, Any]:
        """
        Seed demo memory data for development/testing using the memorize approach.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            Dict with created items count
        """
        await self._ensure_initialized()
        
        logger.info(f"[SEED DEBUG] Starting seed_demo_data for user {user_id}")
        logger.info(f"[SEED DEBUG] Service instance: {id(self._service)}")

        # Create demo conversations that will be extracted into memories
        demo_conversations = [
            [
                {"role": "user", "content": "Hi! My name is Alex and I'm a researcher interested in AI applications."},
                {"role": "assistant", "content": "Nice to meet you Alex! That's fascinating - what areas of AI are you focusing on?"},
                {"role": "user", "content": "I work primarily with NLP and machine learning frameworks in Python."},
            ],
            [
                {"role": "user", "content": "I prefer concise and direct responses - no fluff please."},
                {"role": "assistant", "content": "Got it, I'll keep my responses focused and to the point."},
                {"role": "user", "content": "Also, I value accuracy over speed. Take your time to be precise."},
            ],
            [
                {"role": "user", "content": "When explaining technical concepts, I really appreciate code examples."},
                {"role": "assistant", "content": "That makes sense for a developer. I'll include code snippets when relevant."},
                {"role": "user", "content": "Yes, I'm proficient in Python and familiar with vector databases and embedding models."},
            ],
        ]

        all_items = []
        for i, conv in enumerate(demo_conversations):
            logger.info(f"[SEED DEBUG] Processing conversation {i + 1}/{len(demo_conversations)}")
            try:
                result = await self.memorize_conversation(user_id, conv)
                logger.info(f"[SEED DEBUG] Conversation {i + 1} result: {result}")
                if result and result.get("items"):
                    all_items.extend(result["items"])
                    logger.info(f"[SEED DEBUG] Added {len(result['items'])} items, total: {len(all_items)}")
            except Exception as e:
                logger.warning(f"[SEED DEBUG] Failed to memorize conversation {i + 1}: {e}")
        
        logger.info(f"[SEED DEBUG] Seeding complete. Total items created: {len(all_items)}")
        
        # Check what's in the DB after seeding
        try:
            check_result = await self._service.list_memory_items()
            logger.info(f"[SEED DEBUG] Post-seed DB check: {check_result}")
        except Exception as e:
            logger.error(f"[SEED DEBUG] Failed to check DB: {e}")

        return {
            "items_created": len(all_items),
            "items": all_items,
        }


def get_memu_service() -> MemUService:
    """
    Get or create the singleton MemU service instance.
    
    Returns:
        MemUService instance
    """
    global _memu_service
    if _memu_service is None:
        _memu_service = MemUService()
    return _memu_service
