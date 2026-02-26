"""
MemU Service Wrapper

Provides a service layer for cross-conversation memory using memu-py.
Requires PostgreSQL with pgvector for memory storage.
"""
import json
import logging
import os
import tempfile
import threading
import traceback
from typing import Any, Optional

from pydantic import BaseModel
from memu.app import (
    MemoryService,
    LLMConfig,
    LLMProfilesConfig,
    RetrieveConfig,
)
from memu.app.settings import UserConfig

from config.env import USE_POSTGRES, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT

logger = logging.getLogger(__name__)

# Singleton instance
_memu_service: Optional["MemUService"] = None
_init_lock = threading.Lock()


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

        with _init_lock:
            # Double-check after acquiring lock
            if self._initialized:
                return
            self._do_init()

    def _do_init(self):
        try:
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

            # Define user model for scoping memories by user_id
            class DareUserModel(BaseModel):
                user_id: str | None = None

            user_config = UserConfig(model=DareUserModel)

            # MemU requires PostgreSQL — skip initialization if not configured
            if not USE_POSTGRES:
                raise RuntimeError(
                    "MemU requires PostgreSQL (USE_POSTGRES=True). "
                    "Memory features are disabled."
                )

            db_url = f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
            database_config = {
                "metadata_store": {
                    "provider": "postgres",
                    "dsn": db_url,
                    "ddl_mode": "create",
                },
            }
            logger.info(f"MemU initialized with PostgreSQL at: {DB_HOST}:{DB_PORT}/{DB_NAME}")

            # Configure retrieval with RAG method
            retrieve_config = RetrieveConfig(
                method="rag",
                route_intention=False,  # Always retrieve, don't route
            )

            self._service = MemoryService(
                llm_profiles=llm_profiles,
                database_config=database_config,
                retrieve_config=retrieve_config,
                user_config=user_config,
            )
            self._initialized = True
            logger.info("MemU service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize MemU service: {e}")
            raise

    async def list_items(self, user_id: str) -> list[dict[str, Any]]:
        """
        List all memory items for a user with their category names.
        
        Args:
            user_id: The user's unique identifier
            
        Returns:
            List of memory item dictionaries with categories populated
        """
        await self._ensure_initialized()
        
        try:
            # List only this user's items from memu
            result = await self._service.list_memory_items(where={"user_id": user_id})
            
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
            
            # Fetch categories and build a lookup map (id -> name)
            categories_result = await self._service.list_memory_categories(where={"user_id": user_id})
            category_map = {}
            if isinstance(categories_result, dict):
                cats = categories_result.get("categories", [])
            elif isinstance(categories_result, (list, tuple)):
                cats = categories_result
            else:
                cats = []
            
            for cat in cats:
                if hasattr(cat, "model_dump"):
                    cat = cat.model_dump()
                elif hasattr(cat, "__dict__"):
                    cat = vars(cat)
                cat_id = cat.get("id")
                cat_name = cat.get("name")
                if cat_id and cat_name:
                    category_map[cat_id] = cat_name
            
            logger.info(f"Category map for user {user_id}: {category_map}")
            
            # Get relations from database (item_id -> category_id mappings)
            relations_map = {}  # item_id -> [category_names]
            try:
                db = self._service.database
                # list_relations() returns a list of relation objects
                relations = db.category_item_repo.list_relations()
                for relation in relations:
                    if hasattr(relation, "model_dump"):
                        rel = relation.model_dump()
                    elif hasattr(relation, "__dict__"):
                        rel = vars(relation)
                    else:
                        rel = relation if isinstance(relation, dict) else {}
                    
                    item_id = rel.get("item_id")
                    category_id = rel.get("category_id")
                    
                    if item_id and category_id:
                        cat_name = category_map.get(category_id)
                        if cat_name:
                            if item_id not in relations_map:
                                relations_map[item_id] = []
                            relations_map[item_id].append(cat_name)
                
                logger.info(f"Relations map has {len(relations_map)} items")
            except Exception as e:
                logger.warning(f"Could not fetch relations: {e}")
            
            # Convert items to dicts and populate categories
            user_items = []
            for item in items_list:
                # Handle both dict and object formats
                if hasattr(item, "model_dump"):
                    item_dict = item.model_dump()
                elif isinstance(item, dict):
                    item_dict = item
                elif hasattr(item, "__dict__") and not isinstance(item, str):
                    item_dict = vars(item)
                else:
                    item_dict = {"id": str(item), "content": str(item), "memory_type": "unknown", "categories": []}
                
                # Populate categories from relations map
                item_id = item_dict.get("id")
                item_dict["categories"] = relations_map.get(item_id, [])
                
                # Add to results
                user_items.append(item_dict)
            
            logger.info(f"Returning {len(user_items)} items for user {user_id}")
            return user_items
        except Exception as e:
            logger.error(f"Failed to list memory items for user {user_id}: {e}")
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

    async def get_item(self, item_id: str, user_id: str) -> Optional[dict[str, Any]]:
        """
        Get a single memory item by ID, scoped to the given user.

        Args:
            item_id: The memory item's unique identifier
            user_id: The user's unique identifier (for ownership verification)

        Returns:
            Memory item dict or None if not found or not owned by user
        """
        await self._ensure_initialized()

        try:
            # Verify ownership: list user's items and check if this item belongs to them
            result = await self._service.list_memory_items(where={"user_id": user_id})
            items_list = result.get("items", []) if isinstance(result, dict) else result if isinstance(result, (list, tuple)) else []

            for item in items_list:
                if hasattr(item, "model_dump"):
                    item_dict = item.model_dump()
                elif isinstance(item, dict):
                    item_dict = item
                elif hasattr(item, "__dict__"):
                    item_dict = vars(item)
                else:
                    continue

                if str(item_dict.get("id")) == str(item_id):
                    return item_dict

            return None
        except Exception as e:
            logger.error(f"Failed to get memory item {item_id} for user {user_id}: {e}")
            raise

    async def delete_item(self, item_id: str, user_id: str) -> bool:
        """
        Delete a memory item, scoped to the given user.

        Args:
            item_id: The memory item's unique identifier
            user_id: The user's unique identifier (for ownership verification)

        Returns:
            True if deleted successfully

        Raises:
            PermissionError: If the item does not belong to the user
        """
        await self._ensure_initialized()

        try:
            # Verify ownership before deleting
            item = await self.get_item(item_id, user_id)
            if item is None:
                raise PermissionError(
                    f"Memory item {item_id} not found or not owned by user {user_id}"
                )

            await self._service.delete_memory_item(
                memory_id=item_id,
                user={"user_id": user_id},
            )
            return True
        except PermissionError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete memory item {item_id} for user {user_id}: {e}")
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
                user={"user_id": user_id},
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
                user={"user_id": user_id},
            )
            
            logger.info(f"[SEED DEBUG] memorize returned type: {type(result)}")
            logger.info(f"[SEED DEBUG] memorize returned: {result}")

            # Cleanup temp file
            os.unlink(conv_path)

            return result if result else {"items": [], "categories": [], "resource": None}
        except Exception as e:
            logger.error(f"Failed to memorize conversation for user {user_id}: {e}")
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
        
        # Check what's in the DB after seeding (scoped to this user)
        try:
            check_result = await self._service.list_memory_items(where={"user_id": user_id})
            logger.info(f"[SEED DEBUG] Post-seed DB check for user {user_id}: {check_result}")
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
