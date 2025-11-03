"""WebSocket message DTOs for consumer layer."""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from decimal import Decimal


@dataclass(frozen=True)
class BillingCheckResult:
    """Result from billing validation checks.

    Used to communicate billing check outcomes between billing service
    and consumers, providing clear success/failure status and error details.

    Attributes:
        can_continue: Whether the user has sufficient credits to proceed
        error_code: Short error code if billing check failed
        error_message: Human-readable error message if billing check failed
        current_balance: User's current wallet balance (if applicable)
        required_amount: Amount required for the operation (if applicable)
    """
    can_continue: bool
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    current_balance: Optional[Decimal] = None
    required_amount: Optional[Decimal] = None

    @property
    def has_error(self) -> bool:
        """Check if this result contains an error."""
        return not self.can_continue

    @property
    def error_details(self) -> Optional[Dict[str, Any]]:
        """Get error details as a dictionary."""
        if not self.has_error:
            return None

        details = {}
        if self.current_balance is not None:
            details['current_balance'] = str(self.current_balance)
        if self.required_amount is not None:
            details['required_amount'] = str(self.required_amount)

        return details if details else None


@dataclass
class MessageFinalizationResult:
    """Result from message finalization (billing or budget update).

    Attributes:
        success: Whether the finalization was successful
        message_id: ID of the finalized message
        total_cost: Total cost of the message
        input_tokens: Number of input tokens used
        output_tokens: Number of output tokens used
        error_message: Error message if finalization failed
    """
    success: bool
    message_id: str
    total_cost: Decimal
    input_tokens: int = 0
    output_tokens: int = 0
    error_message: Optional[str] = None

    @property
    def billing_metadata(self) -> Dict[str, Any]:
        """Get billing metadata as a dictionary."""
        return {
            'total_cost': str(self.total_cost),
            'input_tokens': self.input_tokens,
            'output_tokens': self.output_tokens,
        }
