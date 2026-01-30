"""
ElevenLabs Conversational AI service.

Handles all communication with the ElevenLabs API for voice agents.
"""

import logging
import httpx
from typing import Optional, Dict, List, Any

from config import env
from voice.constants import (
    ELEVENLABS_AGENTS_BASE,
    ELEVENLABS_AGENTS_CREATE_ENDPOINT,
    ELEVENLABS_SIGNED_URL_ENDPOINT,
    ELEVENLABS_CONVERSATION_ENDPOINT,
    ELEVENLABS_VOICES_ENDPOINT,
)

logger = logging.getLogger(__name__)


class ElevenLabsService:
    """Service for interacting with ElevenLabs Conversational AI API."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize ElevenLabs service.

        Args:
            api_key: Optional API key override. If not provided, uses env variable.
        """
        self.api_key = api_key or env.ELEVENLABS_API_KEY
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is not configured")

        self.headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    # ============ Agent CRUD Operations ============

    async def create_agent(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new ElevenLabs agent.

        Args:
            config: Agent configuration dict

        Returns:
            Created agent data including agent_id
        """
        logger.info(f"Creating ElevenLabs agent with config: {config}")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                ELEVENLABS_AGENTS_CREATE_ENDPOINT,
                headers=self.headers,
                json=config,
                timeout=30.0
            )
            if response.status_code >= 400:
                logger.error(f"ElevenLabs API error: {response.status_code} - {response.text}")
            response.raise_for_status()
            return response.json()

    async def get_agent(self, agent_id: str) -> Dict[str, Any]:
        """
        Get an existing ElevenLabs agent.

        Args:
            agent_id: ElevenLabs agent ID

        Returns:
            Agent configuration data
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ELEVENLABS_AGENTS_BASE}/{agent_id}",
                headers=self.headers,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    async def update_agent(self, agent_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing ElevenLabs agent.

        Args:
            agent_id: ElevenLabs agent ID
            config: Updated configuration

        Returns:
            Updated agent data
        """
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{ELEVENLABS_AGENTS_BASE}/{agent_id}",
                headers=self.headers,
                json=config,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    async def delete_agent(self, agent_id: str) -> bool:
        """
        Delete an ElevenLabs agent.

        Args:
            agent_id: ElevenLabs agent ID

        Returns:
            True if deletion was successful
        """
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{ELEVENLABS_AGENTS_BASE}/{agent_id}",
                headers=self.headers,
                timeout=30.0
            )
            response.raise_for_status()
            return True

    async def list_agents(self) -> List[Dict[str, Any]]:
        """
        List all ElevenLabs agents.

        Returns:
            List of agent data dicts
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                ELEVENLABS_AGENTS_BASE,
                headers=self.headers,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json().get("agents", [])

    # ============ Conversation Operations ============

    async def get_signed_url(self, agent_id: str) -> str:
        """
        Get a signed URL for WebSocket connection.

        This URL allows the frontend to connect directly to ElevenLabs
        without exposing the API key.

        Args:
            agent_id: ElevenLabs agent ID

        Returns:
            Signed WebSocket URL
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                ELEVENLABS_SIGNED_URL_ENDPOINT,
                headers=self.headers,
                params={"agent_id": agent_id},
                timeout=30.0
            )
            response.raise_for_status()
            return response.json().get("signed_url")

    async def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        """
        Get conversation details including transcript.

        Args:
            conversation_id: ElevenLabs conversation ID

        Returns:
            Conversation data including transcript
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{ELEVENLABS_CONVERSATION_ENDPOINT}/{conversation_id}",
                headers=self.headers,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    # ============ Voice Operations ============

    async def list_voices(self) -> List[Dict[str, Any]]:
        """
        List available voices.

        Returns:
            List of voice data dicts
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                ELEVENLABS_VOICES_ENDPOINT,
                headers=self.headers,
                timeout=30.0
            )
            response.raise_for_status()
            return response.json().get("voices", [])

    # ============ Helper Methods ============

    def build_agent_config(
        self,
        name: str,
        system_prompt: str,
        voice_id: str = "",
        temperature: float = 0.7,
        first_message: str = "",
        language: str = "en",
        max_duration_seconds: int = 1800,
        conversation_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build ElevenLabs agent configuration payload.

        Args:
            name: Agent name
            system_prompt: System prompt for the agent
            voice_id: ElevenLabs voice ID
            temperature: LLM temperature
            first_message: Initial message from agent
            language: Language code
            max_duration_seconds: Max conversation duration
            conversation_config: Full ElevenLabs config (merged with other params).
                Already in snake_case from DRF camel-case parser.

        Returns:
            Configuration dict for ElevenLabs API (snake_case keys)
        """
        # If full config is provided, use it as base
        # Note: DRF's djangorestframework-camel-case already converts
        # incoming camelCase to snake_case, so no conversion needed here
        if conversation_config:
            config = {
                "name": name,
                "conversation_config": conversation_config.copy(),
            }

            # Ensure nested structures exist
            if "agent" not in config["conversation_config"]:
                config["conversation_config"]["agent"] = {}
            if "prompt" not in config["conversation_config"]["agent"]:
                config["conversation_config"]["agent"]["prompt"] = {}
            if "conversation" not in config["conversation_config"]:
                config["conversation_config"]["conversation"] = {}

            # Override with explicit params (these take precedence)
            config["conversation_config"]["agent"]["prompt"]["prompt"] = system_prompt
            config["conversation_config"]["agent"]["prompt"]["temperature"] = temperature

            if first_message:
                config["conversation_config"]["agent"]["first_message"] = first_message

            # Handle TTS config - only include if we have a valid voice_id
            # ElevenLabs requires voice_id if tts section exists
            tts_config = config["conversation_config"].get("tts", {})
            effective_voice_id = voice_id or tts_config.get("voice_id", "")

            if effective_voice_id:
                # Keep tts config but ensure voice_id is set
                if "tts" not in config["conversation_config"]:
                    config["conversation_config"]["tts"] = {}
                config["conversation_config"]["tts"]["voice_id"] = effective_voice_id
            else:
                # Remove tts section entirely if no voice_id - ElevenLabs will use default
                config["conversation_config"].pop("tts", None)

            # Remove turn section if it has invalid/empty values
            turn_config = config["conversation_config"].get("turn", {})
            if turn_config:
                # Clean up turn config - remove empty values
                cleaned_turn = {k: v for k, v in turn_config.items() if v}
                if cleaned_turn:
                    config["conversation_config"]["turn"] = cleaned_turn
                else:
                    config["conversation_config"].pop("turn", None)

            config["conversation_config"]["conversation"]["max_duration_seconds"] = max_duration_seconds

            return config

        # Build from scratch if no full config provided
        config = {
            "name": name,
            "conversation_config": {
                "agent": {
                    "prompt": {
                        "prompt": system_prompt,
                        "temperature": temperature,
                    },
                    "language": language,
                },
                "tts": {},
                "conversation": {
                    "max_duration_seconds": max_duration_seconds,
                },
            },
        }

        if voice_id:
            config["conversation_config"]["tts"]["voice_id"] = voice_id

        if first_message:
            config["conversation_config"]["agent"]["first_message"] = first_message

        return config
