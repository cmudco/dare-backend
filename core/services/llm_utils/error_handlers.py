"""
Error handling and formatting utilities for LLM providers.

This module provides functions to extract concise, user-friendly error messages
from various LLM provider exceptions.
"""

from typing import Dict, Optional


class BaseErrorHandler:
    """Base error handling utilities."""

    @staticmethod
    def check_for_overload_error(error: Exception) -> Optional[str]:
        """
        Check if error is an overload error and return friendly message.

        Args:
            error: Exception to check

        Returns:
            Friendly message if overload error, None otherwise
        """
        # Check error body
        try:
            body = getattr(error, "body", None)
            if isinstance(body, dict):
                err = body.get("error")
                if isinstance(err, dict):
                    err_type = (err.get("type") or "").lower()
                    if err_type == "overloaded_error":
                        return True
        except Exception:
            pass

        # Check response
        try:
            resp = getattr(error, "response", None)
            if resp is not None:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        err = data.get("error")
                        if isinstance(err, dict):
                            err_type = (err.get("type") or "").lower()
                            if err_type == "overloaded_error":
                                return True
                except Exception:
                    pass
        except Exception:
            pass

        # Check string representation
        if "overload" in str(error).lower():
            return True

        return False

    @staticmethod
    def extract_message_from_body(body: Dict) -> Optional[str]:
        """
        Extract error message from error body dictionary.

        Args:
            body: Error body dictionary

        Returns:
            Extracted message or None
        """
        # Try nested error object first
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or err.get("type")
            if isinstance(msg, str) and msg:
                return msg

        # Try top-level fields
        for key in ("message", "detail", "error"):
            val = body.get(key)
            if isinstance(val, str) and val:
                return val

        return None

    @staticmethod
    def extract_message_from_response(response) -> Optional[str]:
        """
        Extract error message from HTTP response.

        Args:
            response: HTTP response object

        Returns:
            Extracted message or None
        """
        try:
            data = response.json()
            if isinstance(data, dict):
                return BaseErrorHandler.extract_message_from_body(data)
        except Exception:
            try:
                text = getattr(response, "text", "")
                if text:
                    return text[:200]
            except Exception:
                pass

        return None


class OpenAIErrorHandler:
    """OpenAI-specific error handling."""

    @staticmethod
    def format_error(error: Exception) -> str:
        """
        Extract a concise error message from OpenAI exceptions.

        Args:
            error: OpenAI exception

        Returns:
            Formatted error message
        """
        # Check for overload first
        if BaseErrorHandler.check_for_overload_error(error):
            return "Due to high traffic, openai services are un-available"

        # Try to extract from response
        resp = getattr(error, "response", None)
        if resp is not None:
            msg = BaseErrorHandler.extract_message_from_response(resp)
            if msg:
                return f"OpenAI error: {msg}"

        # Try message attribute
        msg = getattr(error, "message", None)
        if isinstance(msg, str) and msg:
            return f"OpenAI error: {msg}"

        # Fallback to string representation
        return f"OpenAI error: {str(error)}"


class ClaudeErrorHandler:
    """Claude-specific error handling."""

    @staticmethod
    def format_error(error: Exception) -> str:
        """
        Extract a concise error message from Anthropic exceptions.

        Args:
            error: Anthropic exception

        Returns:
            Formatted error message
        """
        # Check for overload first
        if BaseErrorHandler.check_for_overload_error(error):
            return "Due to high traffic, claude services are un-available"

        # Try to extract from body
        body = getattr(error, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                msg = err.get("message")
                err_type = err.get("type")
                if isinstance(msg, str) and msg:
                    if err_type:
                        return f"Claude error ({err_type}): {msg}"
                    return f"Claude error: {msg}"

            msg = BaseErrorHandler.extract_message_from_body(body)
            if msg:
                return f"Claude error: {msg}"

        # Try to extract from response
        resp = getattr(error, "response", None)
        if resp is not None:
            msg = BaseErrorHandler.extract_message_from_response(resp)
            if msg:
                return f"Claude error: {msg}"

        # Try message attribute
        msg_attr = getattr(error, "message", None)
        if isinstance(msg_attr, str) and msg_attr:
            return f"Claude error: {msg_attr}"

        # Fallback to string representation
        return f"Claude error: {str(error)}"


class GeminiErrorHandler:
    """Gemini-specific error handling."""

    @staticmethod
    def format_error(error: Exception) -> str:
        """
        Extract a concise error message from Gemini exceptions.

        Args:
            error: Gemini exception

        Returns:
            Formatted error message
        """
        # Check for overload first
        if BaseErrorHandler.check_for_overload_error(error):
            return "Due to high traffic, gemini services are un-available"

        # Try to extract from response
        resp = getattr(error, "response", None)
        if resp is not None:
            msg = BaseErrorHandler.extract_message_from_response(resp)
            if msg:
                return f"Gemini error: {msg}"

        # Try message attribute
        msg = getattr(error, "message", None)
        if isinstance(msg, str) and msg:
            return f"Gemini error: {msg}"

        # Fallback to string representation
        return f"Gemini error: {str(error)}"
