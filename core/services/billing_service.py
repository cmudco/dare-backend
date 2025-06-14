from decimal import Decimal
from typing import Dict
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from channels.db import database_sync_to_async
from billing.constants import TransactionTypeChoice
from billing.models import Transaction, Wallet
from conversations.models import LLM, Message
from workflows.models import Step, Workflow, WorkflowRun

import logging

from users.models import User

logger = logging.getLogger(__name__)

class BillingService:
    """Handles wallet balance checks and transaction processing."""

    async def check_sufficient_credits(self, user: 'User', llm: LLM, estimated_input_tokens: int = 500, estimated_output_tokens: int = 1000) -> bool:
        """Check if user has sufficient credits for estimated usage."""
        try:
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

    async def check_streaming_credit_usage(self, user: 'User', llm: LLM, token_usage: Dict) -> tuple:
        """Check if user has sufficient credits during streaming."""
        """Check if user has sufficient credits during streaming."""
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
                            output_tokens=output_tokens
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
                    cost = self._calculate_cost(llm, message_obj.input_tokens, message_obj.output_tokens)
                    message_obj.cost = cost
                    logger.debug(f"Input tokens: {message_obj.input_tokens}, Output tokens: {message_obj.output_tokens}, Cost: {cost}")
                    if cost > Decimal('0.00'):
                        user = message_obj.conversation.user
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
                            transaction_message = f"Message {message_obj.id}: {message_obj.message[:100]}"
                            Transaction.objects.create(
                                user=user,
                                amount=cost,
                                llm=llm,
                                type=TransactionTypeChoice.DEBIT,
                                message=transaction_message,
                                input_tokens=message_obj.input_tokens,
                                output_tokens=message_obj.output_tokens
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

    def process_workflow_billing(self, user: 'User', llm: LLM, input_tokens: int, output_tokens: int, step_id: int) -> bool:
        """Process billing for a workflow step."""
        try:
            cost = self._calculate_cost(llm, input_tokens, output_tokens)

            if cost <= Decimal('0'):
                return True

            wallet = getattr(user, 'wallet', None)
            if not wallet:
                logger.error(f"Wallet not found for user: {user.id}")
                return False

            step = Step.objects.get(id=step_id)
            workflows = step.workflows.all()
            workflow = workflows.first() if workflows.exists() else None

            step_order = step.order
            workflow_title = workflow.title if workflow else "Unknown Workflow"

            transaction_message = f"Workflow {workflow.id} : Title - {workflow_title} | Step #{step_order} "

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
                            output_tokens=output_tokens
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
                        output_tokens=output_tokens
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