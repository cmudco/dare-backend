"""
Socratic Books Dependency Service

Queries the Socratic Books backend to find bots dependent on a given DARE LLM model.
Used to warn admins before deleting a model that would break Socratic Books bots.
Supports nullifying stale model references, deactivating affected bots on deletion,
and notifying bot owners.
"""

import os
import logging

import requests
from typing import Optional, Dict, Any

from django.contrib.auth import get_user_model

from notifications.models import Notification
from notifications.constants import NotificationDeliveryType, NotificationCategory, NotificationAction
from users.constants import AuthSourceChoice

User = get_user_model()
logger = logging.getLogger(__name__)


class SocraticDependencyService:
    """Service for querying Socratic Books for model dependencies and managing model lifecycle."""

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
                    "bot_group_id": b.get("botGroupId") or b.get("bot_group_id"),
                    "bot_group_title": b.get("botGroupTitle") or b.get("bot_group_title", ""),
                    "subject": b.get("subject", ""),
                    "usage_type": b.get("usageType") or b.get("usage_type", ""),
                    "owner_email": b.get("ownerEmail") or b.get("owner_email", ""),
                    "owner_dare_id": b.get("ownerDareId") or b.get("owner_dare_id"),
                }
                for b in bots_raw
            ],
        }

    @classmethod
    def nullify_model_references(cls, model_id: int) -> Optional[Dict[str, Any]]:
        """
        Tell Socratic Books to nullify all references to the given DARE LLM model
        and deactivate the affected bots.

        Called after admin confirms LLM deletion. The operation is idempotent.

        Args:
            model_id: DARE LLM model primary key being deleted.

        Returns:
            Dictionary with affected_bots_count and affected_owner_dare_ids,
            or None if the service is unavailable.
        """
        if not cls.SOCRATIC_BACKEND_URL:
            logger.warning(
                "SOCRATIC_BOTS_BACKEND_URL not configured, skipping model nullification"
            )
            return None

        try:
            url = f"{cls.SOCRATIC_BACKEND_URL}/api/bots/internal/nullify-model/{model_id}/"
            response = requests.post(url, timeout=cls.REQUEST_TIMEOUT)

            if response.status_code == 200:
                return response.json()

            logger.error(
                "Failed to nullify model references: HTTP %s, Response: %s",
                response.status_code,
                response.text,
            )
            return None

        except requests.Timeout:
            logger.error(
                "Timeout nullifying model references for model %s", model_id
            )
            return None

        except requests.RequestException as e:
            logger.error("Request error nullifying model references: %s", str(e))
            return None

        except Exception as e:
            logger.exception(
                "Unexpected error nullifying model references: %s", str(e)
            )
            return None

    @classmethod
    def handle_model_deletion(cls, model_id: int, model_name: str) -> Dict[str, Any]:
        """
        Orchestrate all Socratic Books side effects of an LLM deletion.

        Steps:
        1. Fetch dependency data from SB (for notification targets).
        2. Nullify stale model references and deactivate affected bots in SB.
        3. Create DARE notifications for each affected bot owner.

        Args:
            model_id: DARE LLM model primary key being deleted.
            model_name: Display name of the LLM model (for notification messages).

        Returns:
            Dictionary with 'nullify_failed' (bool) indicating if SB sync failed.
        """
        result = {"nullify_failed": False}

        dependency_data = cls.get_dependent_bots(model_id)

        nullify_result = cls.nullify_model_references(model_id)
        if nullify_result is None:
            result["nullify_failed"] = True

        if dependency_data and dependency_data.get("dependent_bots_count", 0) > 0:
            cls._notify_affected_owners(dependency_data, model_name)

        return result

    @classmethod
    def _notify_affected_owners(cls, dependency_data: Dict[str, Any], model_name: str) -> None:
        """
        Create per-bot notifications for owners affected by an LLM deletion.

        Each affected bot gets its own notification with an action_url pointing
        to the bot's edit page, so the owner can directly select a new model.

        Args:
            dependency_data: Dictionary from get_dependent_bots().
            model_name: Display name of the deleted LLM model.
        """
        for bot in dependency_data["dependent_bots"]:
            dare_user_id = bot.get("owner_dare_id")
            if not dare_user_id:
                continue

            bot_group_id = bot.get("bot_group_id")
            bot_id = bot.get("bot_id")
            bot_title = bot.get("bot_title", "your bot")

            action_url = None
            if bot_group_id and bot_id:
                action_url = f"/bot-groups/{bot_group_id}/bots/{bot_id}/edit"

            try:
                user = User.objects.get(pk=dare_user_id)
                Notification.objects.create(
                    user=user,
                    title="Bot Model Removed",
                    message=(
                        f'The AI model "{model_name}" used by your bot '
                        f'"{bot_title}" has been removed by an administrator. '
                        f"The bot has been deactivated. Please select a new model "
                        f"and re-activate it."
                    ),
                    delivery_type=NotificationDeliveryType.PANEL,
                    category=NotificationCategory.WARNING,
                    action_type=NotificationAction.NAVIGATE,
                    action_url=action_url,
                    source=AuthSourceChoice.SOCRATIC_BOTS,
                )
            except User.DoesNotExist:
                logger.warning(
                    "Cannot notify DARE user %s for model deletion: user not found",
                    dare_user_id,
                )
