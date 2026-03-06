"""
Socratic Books Dependency Service

Queries the Socratic Books backend to find bots dependent on a given DARE LLM model.
Used to warn admins before deleting a model that would break Socratic Books bots.
"""

import os
import logging

import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class SocraticDependencyService:
    """Service for querying Socratic Books for model dependencies."""

    SOCRATIC_BACKEND_URL = os.getenv('SOCRATIC_BOTS_BACKEND_URL')
    REQUEST_TIMEOUT = 5  # seconds

    @classmethod
    def get_dependent_bots(cls, model_id: int) -> Optional[Dict[str, Any]]:
        """
        Query Socratic Books for bots that depend on the given DARE LLM model ID.

        Args:
            model_id: DARE LLM model primary key

        Returns:
            Dictionary with dependent_bots_count and dependent_bots list,
            or None if the service is unavailable.
        """
        if not cls.SOCRATIC_BACKEND_URL:
            logger.warning(
                "SOCRATIC_BOTS_BACKEND_URL not configured, skipping dependency check"
            )
            return None

        try:
            url = f"{cls.SOCRATIC_BACKEND_URL}/api/bots/internal/model-dependents/{model_id}/"
            response = requests.get(url, timeout=cls.REQUEST_TIMEOUT)

            if response.status_code == 200:
                return cls._normalize_response(response.json())

            logger.error(
                "Failed to check model dependencies: HTTP %s, Response: %s",
                response.status_code,
                response.text,
            )
            return None

        except requests.Timeout:
            logger.error(
                "Timeout checking model dependencies for model %s", model_id
            )
            return None

        except requests.RequestException as e:
            logger.error("Request error checking model dependencies: %s", str(e))
            return None

        except Exception as e:
            logger.exception(
                "Unexpected error checking model dependencies: %s", str(e)
            )
            return None

    @staticmethod
    def _normalize_response(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize camelCase keys from Socratic Books (djangorestframework-camel-case)
        to snake_case for consistent usage in DARE backend.
        """
        bots_raw = data.get("dependentBots") or data.get("dependent_bots", [])
        return {
            "dare_model_id": data.get("dareModelId") or data.get("dare_model_id"),
            "dependent_bots_count": data.get("dependentBotsCount") or data.get("dependent_bots_count", 0),
            "dependent_bots": [
                {
                    "bot_id": b.get("botId") or b.get("bot_id"),
                    "bot_title": b.get("botTitle") or b.get("bot_title", ""),
                    "bot_group_title": b.get("botGroupTitle") or b.get("bot_group_title", ""),
                    "subject": b.get("subject", ""),
                    "usage_type": b.get("usageType") or b.get("usage_type", ""),
                }
                for b in bots_raw
            ],
        }
