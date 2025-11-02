"""
Bot Budget Service

Handles communication with Socratic Books backend to update bot budgets
for public bot conversations. This service manages HTTP calls to the
Socratic Books API to track AI usage costs against bot budgets.
"""

import os
import logging
import requests
from decimal import Decimal
from typing import Optional
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)


class BotBudgetService:
    """Service for managing bot budget updates via Socratic Books backend API."""

    # Configuration
    SOCRATIC_BACKEND_URL = os.getenv('SOCRATIC_BOTS_BACKEND_URL')
    REQUEST_TIMEOUT = 5  # seconds

    @classmethod
    async def update_bot_budget(
        cls,
        bot_id: int,
        cost: Decimal,
        metadata: Optional[dict] = None
    ) -> bool:
        """
        Update bot budget in Socratic Books backend (async wrapper).

        Args:
            bot_id: ID of the bot whose budget to update
            cost: Cost to add to the bot's budget usage
            metadata: Optional metadata about the usage

        Returns:
            True if update successful, False otherwise
        """
        # Skip if no cost or zero cost
        if cost is None or cost == 0:
            logger.debug(f"Skipping bot budget update for bot {bot_id}: zero cost")
            return True

        # Run synchronous HTTP call in thread pool
        return await sync_to_async(cls._update_bot_budget_sync)(bot_id, cost, metadata)

    @classmethod
    def _update_bot_budget_sync(
        cls,
        bot_id: int,
        cost: Decimal,
        metadata: Optional[dict] = None
    ) -> bool:
        """
        Synchronous HTTP call to update bot budget.

        Args:
            bot_id: ID of the bot whose budget to update
            cost: Cost to add to the bot's budget usage
            metadata: Optional metadata about the usage

        Returns:
            True if update successful, False otherwise
        """
        try:
            # Build API URL
            url = f"{cls.SOCRATIC_BACKEND_URL}/api/bots/internal/update-budget/"

            # Prepare request payload
            data = {
                'bot_id': bot_id,
                'cost': float(cost)
            }

            # Include metadata if provided
            if metadata:
                data['metadata'] = metadata

            # Make HTTP POST request
            response = requests.post(url, json=data, timeout=cls.REQUEST_TIMEOUT)

            if response.status_code == 200:
                logger.info(f"Updated budget for bot {bot_id}: +${cost}")
                return True
            else:
                logger.error(
                    f"Failed to update bot budget: HTTP {response.status_code}, "
                    f"Response: {response.text}"
                )
                return False

        except requests.Timeout:
            logger.error(
                f"Timeout updating bot budget for bot {bot_id} "
                f"(timeout: {cls.REQUEST_TIMEOUT}s)"
            )
            return False

        except requests.RequestException as e:
            logger.error(f"Request error updating bot budget for bot {bot_id}: {str(e)}")
            return False

        except Exception as e:
            logger.exception(f"Unexpected error updating bot budget for bot {bot_id}: {str(e)}")
            return False

    @classmethod
    async def check_bot_budget_available(
        cls,
        bot_id: int,
        estimated_cost: Decimal
    ) -> tuple[bool, Optional[str]]:
        """
        Check if bot has sufficient budget remaining (optional pre-flight check).

        Args:
            bot_id: ID of the bot to check
            estimated_cost: Estimated cost of the operation

        Returns:
            Tuple of (has_budget, error_message)
        """
        # This is a placeholder for future implementation
        # Currently, we don't do pre-flight budget checks for public bots
        # Budget is tracked after the fact
        return True, None
