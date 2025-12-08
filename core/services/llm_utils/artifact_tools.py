"""
Artifact tool definitions for LLM providers.

This module provides tool definitions for artifact generation,
enabling structured creation, updating, and finalization of long-form content.
"""

import json
from typing import Dict, List, Optional


class ArtifactTools:
    """Tool definitions for artifact generation."""

    # Tool names
    CREATE_ARTIFACT = "create_artifact"
    UPDATE_ARTIFACT = "update_artifact"
    FINALIZE_ARTIFACT = "finalize_artifact"
    APPEND_SECTIONS = "append_sections"  # For modification mode

    @staticmethod
    def get_create_artifact_tool() -> Dict:
        """
        Get the create_artifact tool definition for LLM function calling.

        This tool is used by the LLM to initialize a new artifact with
        a structured outline and metadata.

        Returns:
            Tool definition dictionary
        """
        return {
            "type": "function",
            "function": {
                "name": "create_artifact",
                "description": (
                    "Initialize a new artifact for generating long-form, structured content. "
                    "Use this when the user requests comprehensive documents, tutorials, "
                    "detailed guides, or code with multiple components. The artifact will "
                    "be generated section by section with the ability to pause and resume."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "artifact_type": {
                            "type": "string",
                            "enum": ["document", "code", "diagram"],
                            "description": "Type of artifact to create"
                        },
                        "title": {
                            "type": "string",
                            "description": "Title of the artifact"
                        },
                        "outline": {
                            "type": "string",
                            "description": (
                                "Structured outline of the artifact with numbered sections. "
                                "Each section should be on a new line with format: "
                                "'1. Section Title - Brief description'"
                            )
                        },
                        "estimated_sections": {
                            "type": "integer",
                            "description": "Estimated number of sections in the artifact",
                            "minimum": 1,
                            "maximum": 50
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language for code artifacts (optional)"
                        }
                    },
                    "required": ["artifact_type", "title", "outline", "estimated_sections"]
                }
            }
        }

    @staticmethod
    def get_update_artifact_tool() -> Dict:
        """
        Get the update_artifact tool definition for LLM function calling.

        This tool is used by the LLM to append content to an artifact
        during section-by-section generation.

        Returns:
            Tool definition dictionary
        """
        return {
            "type": "function",
            "function": {
                "name": "update_artifact",
                "description": (
                    "Append content to the current artifact. Use this to add the next "
                    "section of content. Each call adds content for one section."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "The content to append to the artifact"
                        },
                        "section_title": {
                            "type": "string",
                            "description": "Title of the current section being added"
                        },
                        "section_number": {
                            "type": "integer",
                            "description": "The section number being generated",
                            "minimum": 1
                        }
                    },
                    "required": ["content", "section_number"]
                }
            }
        }

    @staticmethod
    def get_finalize_artifact_tool() -> Dict:
        """
        Get the finalize_artifact tool definition for LLM function calling.

        This tool is used by the LLM to mark an artifact as complete
        and optionally provide a summary.

        Returns:
            Tool definition dictionary
        """
        return {
            "type": "function",
            "function": {
                "name": "finalize_artifact",
                "description": (
                    "Mark the artifact as complete. Use this when all sections have "
                    "been generated and the artifact is ready for the user."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Brief summary of what was created"
                        }
                    },
                    "required": ["summary"]
                }
            }
        }

    @staticmethod
    def get_append_sections_tool() -> Dict:
        """
        Get the append_sections tool definition for LLM function calling.

        This tool is used by the LLM to plan new sections to append
        to an existing artifact during modification mode.

        Returns:
            Tool definition dictionary
        """
        return {
            "type": "function",
            "function": {
                "name": "append_sections",
                "description": (
                    "Plan new sections to append to the END of an existing artifact. "
                    "Analyze the user request and existing content to determine what "
                    "new sections should be added. The sections will be appended after "
                    "the current content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "new_sections_outline": {
                            "type": "string",
                            "description": (
                                "Outline of NEW sections to append. Continue numbering "
                                "from existing sections. Format each section as: "
                                "'N. Section Title - Brief description' on separate lines."
                            )
                        },
                        "estimated_new_sections": {
                            "type": "integer",
                            "description": "Number of new sections to add",
                            "minimum": 1,
                            "maximum": 20
                        }
                    },
                    "required": ["new_sections_outline", "estimated_new_sections"]
                }
            }
        }

    @classmethod
    def get_planning_tools(cls) -> List[Dict]:
        """
        Get tools for artifact planning phase.

        Returns:
            List of tool definitions for planning
        """
        return [cls.get_create_artifact_tool()]

    @classmethod
    def get_modification_planning_tools(cls) -> List[Dict]:
        """
        Get tools for artifact modification planning phase.

        Returns:
            List of tool definitions for modification planning
        """
        return [cls.get_append_sections_tool()]

    @classmethod
    def get_generation_tools(cls) -> List[Dict]:
        """
        Get tools for artifact generation phase.

        Returns:
            List of tool definitions for generation
        """
        return [
            cls.get_update_artifact_tool(),
            cls.get_finalize_artifact_tool()
        ]

    @classmethod
    def get_all_tools(cls) -> List[Dict]:
        """
        Get all artifact tools.

        Returns:
            List of all tool definitions
        """
        return [
            cls.get_create_artifact_tool(),
            cls.get_update_artifact_tool(),
            cls.get_finalize_artifact_tool()
        ]

    @staticmethod
    def is_artifact_tool_call(tool_name: str) -> bool:
        """
        Check if a tool call is an artifact-related tool.

        Args:
            tool_name: Name of the tool being called

        Returns:
            True if this is an artifact tool
        """
        return tool_name in [
            ArtifactTools.CREATE_ARTIFACT,
            ArtifactTools.UPDATE_ARTIFACT,
            ArtifactTools.FINALIZE_ARTIFACT,
            ArtifactTools.APPEND_SECTIONS,
        ]

    @staticmethod
    def parse_tool_arguments(arguments: str) -> Dict:
        """
        Parse tool call arguments from JSON string.

        Args:
            arguments: JSON string of arguments

        Returns:
            Parsed arguments dictionary
        """
        try:
            return json.loads(arguments)
        except json.JSONDecodeError:
            return {}


