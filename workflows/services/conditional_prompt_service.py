"""
Service for generating provider-specific prompts for conditional node routing.

This service handles creating appropriate evaluation prompts based on the LLM provider,
allowing for future customization of structured outputs per provider.
"""
from typing import List, Dict


class ConditionalPromptService:
    """
    Service to generate routing evaluation prompts for different LLM providers.

    Currently all providers use the same XML-based prompt format (optimized for Claude),
    but this service provides extensibility points for future provider-specific
    structured output formats (e.g., OpenAI's structured outputs, Gemini's function calling).
    """

    @staticmethod
    def get_prompt_for_openai(
        evaluation_prompt: str,
        routes: List[Dict[str, str]],
        input_text: str
    ) -> str:
        """
        Generate routing evaluation prompt for OpenAI models.

        Currently uses XML format. Future enhancement: Use OpenAI's structured outputs
        with JSON schema for more reliable parsing.

        Args:
            evaluation_prompt: Custom evaluation instructions
            routes: List of route definitions with 'name' and 'description'
            input_text: The input text to evaluate

        Returns:
            Formatted prompt string
        """
        return ConditionalPromptService._get_xml_prompt(
            evaluation_prompt, routes, input_text
        )

    @staticmethod
    def get_prompt_for_claude(
        evaluation_prompt: str,
        routes: List[Dict[str, str]],
        input_text: str
    ) -> str:
        """
        Generate routing evaluation prompt for Anthropic Claude models.

        Uses XML format as Claude doesn't support structured outputs natively.
        Claude performs well with XML-structured prompts.

        Args:
            evaluation_prompt: Custom evaluation instructions
            routes: List of route definitions with 'name' and 'description'
            input_text: The input text to evaluate

        Returns:
            Formatted prompt string
        """
        return ConditionalPromptService._get_xml_prompt(
            evaluation_prompt, routes, input_text
        )

    @staticmethod
    def get_prompt_for_gemini(
        evaluation_prompt: str,
        routes: List[Dict[str, str]],
        input_text: str
    ) -> str:
        """
        Generate routing evaluation prompt for Google Gemini models.

        Currently uses XML format. Future enhancement: Use Gemini's function calling
        for more structured routing decisions.

        Args:
            evaluation_prompt: Custom evaluation instructions
            routes: List of route definitions with 'name' and 'description'
            input_text: The input text to evaluate

        Returns:
            Formatted prompt string
        """
        return ConditionalPromptService._get_xml_prompt(
            evaluation_prompt, routes, input_text
        )

    @staticmethod
    def _get_xml_prompt(
        evaluation_prompt: str,
        routes: List[Dict[str, str]],
        input_text: str
    ) -> str:
        """
        Generate XML-formatted routing evaluation prompt.

        This is the baseline format used across all providers currently.

        Args:
            evaluation_prompt: Custom evaluation instructions
            routes: List of route definitions with 'name' and 'description'
            input_text: The input text to evaluate

        Returns:
            XML-formatted prompt string
        """
        route_xml_elements = "\n".join([
            f'<route name="{route["name"]}">{route.get("description", route["name"])}</route>'
            for route in routes
        ])

        return f"""{evaluation_prompt}

Based on the following input, evaluate and choose the most appropriate route.

<routes>
{route_xml_elements}
</routes>

<input>
{input_text}
</input>

Analyze the input carefully and respond in this EXACT format (do not deviate):
<analysis>
[Brief reasoning for your choice - 1-2 sentences]
</analysis>
<decision>[EXACT route name from the routes listed above]</decision>"""

    @staticmethod
    def get_prompt_for_provider(
        provider: str,
        evaluation_prompt: str,
        routes: List[Dict[str, str]],
        input_text: str
    ) -> str:
        """
        Get the appropriate prompt based on LLM provider.

        Args:
            provider: LLM provider name ('openai', 'claude', 'gemini', etc.)
            evaluation_prompt: Custom evaluation instructions
            routes: List of route definitions with 'name' and 'description'
            input_text: The input text to evaluate

        Returns:
            Provider-specific formatted prompt string
        """
        provider_lower = provider.lower()

        if provider_lower == 'openai':
            return ConditionalPromptService.get_prompt_for_openai(
                evaluation_prompt, routes, input_text
            )
        elif provider_lower == 'claude':
            return ConditionalPromptService.get_prompt_for_claude(
                evaluation_prompt, routes, input_text
            )
        elif provider_lower == 'gemini':
            return ConditionalPromptService.get_prompt_for_gemini(
                evaluation_prompt, routes, input_text
            )
        else:
            return ConditionalPromptService._get_xml_prompt(
                evaluation_prompt, routes, input_text
            )
