from decimal import Decimal
from typing import Dict
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async
from billing.constants import TransactionTypeChoice
from billing.models import Transaction, Wallet
from conversations.models import LLM, Message
from core.services.energy_service import compute_impact
from workflows.models import Workflow, WorkflowRun, WorkflowNode
from api_keys.constants import BillingModeChoice
from users.constants import AuthSourceChoice

import logging

from users.models import User

logger = logging.getLogger(__name__)

class BillingService:
    """Handles wallet balance checks and transaction processing."""

    @staticmethod
    def _compute_energy_impact(message_obj: Message) -> dict:
        """Compute energy impact for a message and return values as Decimals.

        Returns a dict with energy_wh, carbon_g, water_ml ready to set on
        Message or pass to Transaction.objects.create().
        Returns empty dict if computation is not applicable.
        """
        llm = message_obj.llm
        output_tokens = message_obj.output_tokens
        if not llm or not output_tokens:
            return {}

        try:
            impact = compute_impact(
                output_tokens=output_tokens,
                provider_name=llm.provider,
                model_name=llm.identifier,
            )
            if impact.energy_wh == 0.0:
                return {}

            return {
                "energy_wh": Decimal(str(round(impact.energy_wh, 6))),
                "carbon_g": Decimal(str(round(impact.carbon_g, 6))),
                "water_ml": Decimal(str(round(impact.water_ml, 6))),
            }
        except Exception as e:
            logger.warning("Energy impact computation failed: %s", e)
            return {}

    async def check_sufficient_credits(self, user: 'User', llm: LLM, estimated_input_tokens: int = 500, estimated_output_tokens: int = 1000) -> bool:
        """
        Check if user has sufficient credits for estimated usage.

        For users in OWN_API mode: Always returns True (they use their own API keys)
        For users in WALLET mode: Checks wallet balance
        """
        try:
            billing_mode = await database_sync_to_async(lambda: user.billing_mode)()
            if billing_mode == BillingModeChoice.OWN_API:
                logger.info(f"User {user.id} in OWN_API mode - skipping wallet balance check")
                return True

            wallet = await self._get_user_wallet(user)
            if not wallet:
                logger.error(f"Wallet not found for user: {user.id}")
                await self._send_error("wallet_not_found", "User wallet not found")
                return False

            balance = await database_sync_to_async(lambda: wallet.balance)()
            estimated_cost = self._calculate_estimated_cost(llm, estimated_input_tokens, estimated_output_tokens)

            if balance < estimated_cost.quantize(Decimal('0.01')):
                logger.warning(f"Insufficient credits for user: {user.id}, balance: {balance}, required: {estimated_cost}")
                await self._send_error(
                    "insufficient_credits",
                    "Insufficient wallet balance",
                    {"current_balance": str(balance), "required_amount": str(estimated_cost)}
                )
                return False

            if balance < (estimated_cost * Decimal('1.5')).quantize(Decimal('0.01')):
                logger.info(f"Low balance warning for user: {user.id}, balance: {balance}, estimated: {estimated_cost}")
                await self._send_warning(
                    "low_balance",
                    "Running low on credits",
                    {"current_balance": str(balance), "estimated_amount": str(estimated_cost)}
                )
            return True

        except Exception as e:
            logger.exception(f"Error checking credits for user: {user.id}: {str(e)}")
            await self._send_error("credit_check_error", "Error checking credits")
            return False

    async def check_streaming_credit_usage(self, user: 'User', llm: LLM, token_usage: Dict, platform: str = None) -> tuple:
        """
        Check if user has sufficient credits during streaming.

        Note: This is a legacy method kept for backward compatibility.
        Prefer using finalize_ai_message which auto-detects platform from conversation.

        Args:
            user: User object
            llm: LLM model being used
            token_usage: Token usage dictionary
            platform: Platform source (optional), defaults to user's auth_source
        """
        try:
            wallet = await self._get_user_wallet(user)
            if not wallet:
                logger.error(f"Wallet not found for user: {user.id}")
                return False, {"error": "wallet_not_found", "message": "User wallet not found"}

            balance = await database_sync_to_async(lambda: wallet.balance)()
            input_tokens = token_usage.get('input_tokens', 0)
            output_tokens = token_usage.get('output_tokens', 0)
            message_id = token_usage.get('message_id')
            message_content = token_usage.get('message_content')

            # Determine platform: use provided value or fall back to user's auth_source
            if platform is None:
                platform = await database_sync_to_async(lambda: user.auth_source)()

            # Check if direct cost is provided (e.g., for image generation)
            if 'cost' in token_usage:
                estimated_cost = Decimal(str(token_usage['cost']))
            else:
                estimated_cost = self._calculate_cost(llm, input_tokens, output_tokens)

            if estimated_cost > balance:
                if balance > Decimal('0'):
                    amount_to_deduct = balance
                    transaction_message = (
                        f"Message {message_id}: {message_content[:100]}"
                        if message_id and message_content
                        else "Partial LLM usage (streaming)"
                    )
                    await database_sync_to_async(
                        lambda: Transaction.objects.create(
                            user=user,
                            message=transaction_message,
                            amount=amount_to_deduct,
                            type=TransactionTypeChoice.DEBIT,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            billing_mode=user.billing_mode,
                            platform=platform
                        )
                    )()
                    await database_sync_to_async(lambda: wallet.refresh_from_db())()
                    updated_balance = await database_sync_to_async(lambda: wallet.balance)()
                    logger.warning(f"Interrupted stream for user: {user.id}, balance: {updated_balance}")
                    return False, {
                        "error": "insufficient_balance",
                        "message": "Insufficient balance to continue",
                        "current_balance": str(updated_balance),
                        "required_amount": str(estimated_cost)
                    }
                return False, {
                    "error": "insufficient_balance",
                    "message": "Insufficient balance to continue",
                    "current_balance": str(balance),
                    "required_amount": str(estimated_cost)
                }
            return True, None

        except Exception as e:
            logger.exception(f"Error checking streaming credits for user: {user.id}: {str(e)}")
            return False, {"error": "credit_check_error", "message": "Error checking credits"}

    def finalize_ai_message(self, message_obj: Message, ai_response: str, token_usage: Dict) -> Message:
        """
        Finalize AI message and handle billing based on user's billing mode.

        Platform is automatically determined from the conversation's source field.

        For OWN_API mode: Creates tracking transaction with $0.00 amount
        For WALLET mode: Deducts from user's wallet

        Args:
            message_obj: Message object to finalize
            ai_response: AI response text
            token_usage: Dictionary with input_tokens, output_tokens, and optional cost
        """
        if not message_obj:
            return None

        try:
            message_obj.message = ai_response
            cost = Decimal('0.000000')

            if token_usage:
                message_obj.input_tokens = token_usage.get("input_tokens", 0)
                message_obj.output_tokens = token_usage.get("output_tokens", 0)
                llm = message_obj.llm
                if llm:
                    # Check if direct cost is provided (e.g., for image generation)
                    if 'cost' in token_usage:
                        cost = Decimal(str(token_usage['cost']))
                    else:
                        cost = self._calculate_cost(llm, message_obj.input_tokens, message_obj.output_tokens)
                    message_obj.cost = cost
                    logger.debug(f"Input tokens: {message_obj.input_tokens}, Output tokens: {message_obj.output_tokens}, Cost: {cost}")

                    # Compute energy/environmental impact
                    energy_data = self._compute_energy_impact(message_obj)
                    if energy_data:
                        message_obj.energy_wh = energy_data["energy_wh"]
                        message_obj.carbon_g = energy_data["carbon_g"]
                        message_obj.water_ml = energy_data["water_ml"]

                    if cost > Decimal('0.00'):
                        user = message_obj.conversation.user

                        # Determine platform from conversation's authoritative source field
                        transaction_platform = message_obj.conversation.source

                        # Check user's billing mode
                        if user.billing_mode == BillingModeChoice.OWN_API:
                            # User is using their own API key - create tracking transaction with $0
                            logger.info(f"User {user.id} in OWN_API mode - creating tracking transaction")
                            with db_transaction.atomic():
                                # Special message for image generation
                                if token_usage.get('cost') and message_obj.input_tokens == 0 and message_obj.output_tokens == 0:
                                    transaction_message = f"Image Generation ({llm.name}): {message_obj.message[:50]} (Own API Key - Cost: ${cost})"
                                else:
                                    transaction_message = f"Message {message_obj.id}: {message_obj.message[:100]} (Own API Key)"
                                Transaction.objects.create(
                                    user=user,
                                    amount=Decimal('0.00'),
                                    llm=llm,
                                    type=TransactionTypeChoice.DEBIT,
                                    message=transaction_message,
                                    input_tokens=message_obj.input_tokens,
                                    output_tokens=message_obj.output_tokens,
                                    billing_mode=BillingModeChoice.OWN_API,
                                    platform=transaction_platform,
                                    **energy_data,
                                )
                        else:
                            # WALLET mode - charge user's wallet
                            wallet = getattr(user, 'wallet', None)
                            if not wallet:
                                raise ValidationError({
                                    "error": "wallet_not_found",
                                    "message": "User wallet not found"
                                })
                            if wallet.balance < cost:
                                raise ValidationError({
                                    "error": "insufficient_balance",
                                    "message": "Insufficient wallet balance",
                                    "current_balance": str(wallet.balance),
                                    "required_amount": str(cost)
                                })

                            with db_transaction.atomic():
                                # Special message for image generation
                                if token_usage.get('cost') and message_obj.input_tokens == 0 and message_obj.output_tokens == 0:
                                    transaction_message = f"Image Generation ({llm.name}): {message_obj.message[:50]} - ${cost}"
                                else:
                                    transaction_message = f"Message {message_obj.id}: {message_obj.message[:100]}"
                                Transaction.objects.create(
                                    user=user,
                                    amount=cost,
                                    llm=llm,
                                    type=TransactionTypeChoice.DEBIT,
                                    message=transaction_message,
                                    input_tokens=message_obj.input_tokens,
                                    output_tokens=message_obj.output_tokens,
                                    billing_mode=BillingModeChoice.WALLET,
                                    platform=transaction_platform,
                                    **energy_data,
                                )
                                wallet.refresh_from_db()
            message_obj.save()
            return message_obj
        except ValidationError as e:
            logger.error(f"Validation error finalizing message: {str(e)}")
            raise
        except Exception as e:
            logger.exception(f"Error finalizing message: {str(e)}")
            raise ValidationError({"error": "billing_error", "message": "Failed to process billing"})

    def finalize_ai_message_no_billing(self, message_obj: Message, ai_response: str, token_usage: Dict) -> tuple[Message, Decimal]:
        """
        Finalize AI message WITHOUT billing (for public bot conversations).

        Calculates cost and updates message with token usage, but does NOT:
        - Create transactions
        - Deduct from wallet
        - Check billing mode

        Used by PublicBotConsumer where bot budget is tracked separately.

        Args:
            message_obj: Message object to finalize
            ai_response: AI response text
            token_usage: Dictionary with input_tokens, output_tokens, and optional cost

        Returns:
            Tuple of (updated_message, calculated_cost)
        """
        if not message_obj:
            return None, Decimal('0')

        try:
            message_obj.message = ai_response
            cost = Decimal('0.000000')

            if token_usage:
                message_obj.input_tokens = token_usage.get("input_tokens", 0)
                message_obj.output_tokens = token_usage.get("output_tokens", 0)
                llm = message_obj.llm

                if llm:
                    # Check if direct cost is provided (e.g., for image generation)
                    if 'cost' in token_usage:
                        cost = Decimal(str(token_usage['cost']))
                    else:
                        cost = self._calculate_cost(llm, message_obj.input_tokens, message_obj.output_tokens)

                    message_obj.cost = cost
                    logger.debug(
                        f"Public bot message - Input tokens: {message_obj.input_tokens}, "
                        f"Output tokens: {message_obj.output_tokens}, Cost: {cost}"
                    )

                    # Compute energy/environmental impact
                    energy_data = self._compute_energy_impact(message_obj)
                    if energy_data:
                        message_obj.energy_wh = energy_data["energy_wh"]
                        message_obj.carbon_g = energy_data["carbon_g"]
                        message_obj.water_ml = energy_data["water_ml"]

            message_obj.save()
            return message_obj, cost

        except Exception as e:
            logger.exception(f"Error finalizing message (no billing): {str(e)}")
            raise ValidationError({"error": "finalization_error", "message": "Failed to finalize message"})

    def process_workflow_billing(self, user: 'User', llm: LLM, input_tokens: int, output_tokens: int, step_node_id: int = None) -> bool:
        """
        Process billing for a workflow step or routing node.

        Note: Workflows are DARE-only feature, so platform is always DARE.
        """
        try:
            cost = self._calculate_cost(llm, input_tokens, output_tokens)

            if cost <= Decimal('0'):
                return True

            wallet = getattr(user, 'wallet', None)
            if not wallet:
                logger.error(f"Wallet not found for user: {user.id}")
                return False

            if step_node_id:
                step_node = WorkflowNode.objects.get(id=step_node_id)
                workflow = step_node.workflow
                node_label = getattr(step_node.data_object, 'label', None) if step_node.data_object else None
            else:
                workflow = None
                node_label = None

            workflow_title = workflow.title if workflow else "Unknown Workflow"
            workflow_id = workflow.id if workflow else "N/A"

            if node_label:
                transaction_message = (
                    f"Workflow {workflow_id} : Title - {workflow_title} | Node {node_label} "
                )
            else:
                transaction_message = f"Workflow {workflow_id} : Title - {workflow_title} | Routing Node "

            if wallet.balance < cost:
                amount_to_deduct = wallet.balance
                if amount_to_deduct > Decimal('0'):
                    with db_transaction.atomic():
                        Transaction.objects.create(
                            user=user,
                            message=transaction_message + " (insufficient balance)",
                            llm=llm,
                            amount=amount_to_deduct,
                            type=TransactionTypeChoice.DEBIT,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            billing_mode=user.billing_mode,
                            platform=AuthSourceChoice.DARE
                        )
                        wallet.balance = Decimal('0')
                        wallet.save()
                return False
            else:
                with db_transaction.atomic():
                    Transaction.objects.create(
                        user=user,
                        message=transaction_message,
                        amount=cost,
                        llm=llm,
                        type=TransactionTypeChoice.DEBIT,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        billing_mode=user.billing_mode,
                        platform=AuthSourceChoice.DARE
                    )
                    wallet.refresh_from_db()
                return True

        except Exception as e:
            logger.exception(f"Error processing workflow billing: {str(e)}")
            raise ValidationError({"error": "billing_error", "message": "Failed to process billing"})

    def _calculate_estimated_cost(self, llm: LLM, input_tokens: int, output_tokens: int) -> Decimal:
        """Calculate estimated cost based on token usage."""
        input_rate = llm.input_token_rate_per_million / Decimal('1000000')
        output_rate = llm.output_token_rate_per_million / Decimal('1000000')
        return (Decimal(input_tokens) * input_rate) + (Decimal(output_tokens) * output_rate)

    def _calculate_cost(self, llm: LLM, input_tokens: int, output_tokens: int) -> Decimal:
        """Calculate actual cost based on token usage."""
        return self._calculate_estimated_cost(llm, input_tokens, output_tokens)

    async def _get_user_wallet(self, user: 'User') -> 'Wallet':
        """Fetch user wallet, creating one if it doesn't exist."""
        try:
            wallet = await database_sync_to_async(lambda: user.wallet)()
            return wallet
        except user.wallet.RelatedObjectDoesNotExist:
            logger.warning(f"Creating wallet for user: {user.id}")
            wallet = await database_sync_to_async(
                lambda: Wallet.objects.create(user=user, balance=Decimal('5.00'))
            )()
            return wallet

    async def _send_error(self, code: str, message: str, details: Dict = None):
        """Placeholder for error sending (to be implemented in consumer)."""
        pass

    async def _send_warning(self, code: str, message: str, details: Dict = None):
        """Placeholder for warning sending (to be implemented in consumer)."""
        pass

    async def process_message_cost(
        self,
        user: 'User',
        llm: LLM,
        message_obj: Message,
        token_usage: Dict,
    ) -> None:
        """
        Process billing for a message asynchronously.

        This is the async version of finalize_ai_message for use in async contexts
        like artifact generation.

        Args:
            user: User object
            llm: LLM model being used
            message_obj: Message object to bill for
            token_usage: Dictionary with input_tokens, output_tokens, and optional cost
        """
        try:
            # Calculate cost
            input_tokens = token_usage.get('input_tokens', 0)
            output_tokens = token_usage.get('output_tokens', 0)

            if 'cost' in token_usage:
                cost = Decimal(str(token_usage['cost']))
            else:
                cost = self._calculate_cost(llm, input_tokens, output_tokens)

            if cost <= Decimal('0'):
                return

            # Update message with token info
            message_obj.input_tokens = input_tokens
            message_obj.output_tokens = output_tokens
            message_obj.cost = cost

            # Compute energy/environmental impact
            energy_data = await database_sync_to_async(self._compute_energy_impact)(message_obj)
            if energy_data:
                message_obj.energy_wh = energy_data["energy_wh"]
                message_obj.carbon_g = energy_data["carbon_g"]
                message_obj.water_ml = energy_data["water_ml"]

            await database_sync_to_async(message_obj.save)()

            # Get billing mode
            billing_mode = await database_sync_to_async(lambda: user.billing_mode)()

            # Get platform from conversation
            conversation = await database_sync_to_async(lambda: message_obj.conversation)()
            transaction_platform = await database_sync_to_async(lambda: conversation.source)()

            # Capture energy_data for lambda closures
            ed = energy_data

            if billing_mode == BillingModeChoice.OWN_API:
                # User is using their own API key - create tracking transaction with $0
                logger.info(f"User {user.id} in OWN_API mode - creating tracking transaction for artifact")
                await database_sync_to_async(
                    lambda: Transaction.objects.create(
                        user=user,
                        amount=Decimal('0.00'),
                        llm=llm,
                        type=TransactionTypeChoice.DEBIT,
                        message=f"Artifact generation: {message_obj.message[:100]} (Own API Key)",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        billing_mode=BillingModeChoice.OWN_API,
                        platform=transaction_platform,
                        **ed,
                    )
                )()
            else:
                # WALLET mode - charge user's wallet
                wallet = await self._get_user_wallet(user)
                if not wallet:
                    logger.error(f"Wallet not found for user: {user.id}")
                    return

                balance = await database_sync_to_async(lambda: wallet.balance)()
                if balance < cost:
                    logger.warning(f"Insufficient balance for artifact billing: user={user.id}, balance={balance}, cost={cost}")
                    return

                await database_sync_to_async(
                    lambda: Transaction.objects.create(
                        user=user,
                        amount=cost,
                        llm=llm,
                        type=TransactionTypeChoice.DEBIT,
                        message=f"Artifact generation: {message_obj.message[:100]}",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        billing_mode=BillingModeChoice.WALLET,
                        platform=transaction_platform,
                        **ed,
                    )
                )()

                logger.info(f"Billed user {user.id} ${cost} for artifact generation")

        except Exception as e:
            logger.exception(f"Error processing artifact message cost: {str(e)}")

    async def check_credits_for_amount(self, user: 'User', amount: Decimal) -> bool:
        """
        Check if user has sufficient credits for a specific dollar amount.

        Useful for non-LLM operations like image generation.

        Args:
            user: User object
            amount: Dollar amount to check (Decimal)

        Returns:
            True if user has sufficient credits, False otherwise
        """
        try:
            billing_mode = await database_sync_to_async(lambda: user.billing_mode)()
            if billing_mode == BillingModeChoice.OWN_API:
                logger.info(f"User {user.id} in OWN_API mode - skipping wallet balance check for amount ${amount}")
                return True

            wallet = await self._get_user_wallet(user)
            if not wallet:
                logger.error(f"Wallet not found for user: {user.id}")
                return False

            balance = await database_sync_to_async(lambda: wallet.balance)()

            if balance < amount.quantize(Decimal('0.01')):
                logger.warning(f"Insufficient credits for user: {user.id}, balance: {balance}, required: {amount}")
                return False

            return True

        except Exception as e:
            logger.exception(f"Error checking credits for amount for user: {user.id}: {str(e)}")
            return False

    async def deduct_credits(self, user: 'User', amount: Decimal, description: str = "Service usage", platform: str = None):
        """
        Deduct a specific amount from user's wallet.

        Note: For message-based billing, prefer using finalize_ai_message
        which auto-detects platform from conversation.

        Args:
            user: User object
            amount: Amount to deduct (Decimal)
            description: Description for transaction
            platform: Platform source (optional), defaults to user's auth_source

        Raises:
            ValidationError if insufficient balance or billing error
        """
        try:
            billing_mode = await database_sync_to_async(lambda: user.billing_mode)()
            if billing_mode == BillingModeChoice.OWN_API:
                logger.info(f"User {user.id} in OWN_API mode - skipping deduction for ${amount}")
                return

            wallet = await self._get_user_wallet(user)
            if not wallet:
                raise ValidationError({"error": "wallet_not_found", "message": "User wallet not found"})

            balance = await database_sync_to_async(lambda: wallet.balance)()

            if balance < amount:
                raise ValidationError({
                    "error": "insufficient_credits",
                    "message": f"Insufficient balance: ${balance}, required: ${amount}"
                })

            # Determine platform: use provided value or fall back to user's auth_source
            if platform is None:
                platform = await database_sync_to_async(lambda: user.auth_source)()

            # Create transaction and deduct
            await database_sync_to_async(
                lambda: Transaction.objects.create(
                    user=user,
                    message=description,
                    amount=amount,
                    type=TransactionTypeChoice.DEBIT,
                    billing_mode=user.billing_mode,
                    platform=platform
                )
            )()

            logger.info(f"Deducted ${amount} from user {user.id} wallet for: {description}")

        except ValidationError:
            raise
        except Exception as e:
            logger.exception(f"Error deducting credits for user: {user.id}: {str(e)}")
            raise ValidationError({"error": "billing_error", "message": "Failed to process payment"})
