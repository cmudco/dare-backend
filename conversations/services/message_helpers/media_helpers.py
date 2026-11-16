"""
Media Helpers Module

Functions for processing images, files, and media content.
Handles image generation responses and file processing.
"""

from typing import Dict, Any


def build_generated_image_data(
    generated_file, prompt: str, usage: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Build generated image response data for frontend.

    Args:
        generated_file: Saved File instance with image data
        prompt: Original generation prompt
        usage: Usage dict with image metadata

    Returns:
        Dict with image data for frontend
    """
    return {
        "fileId": generated_file.id,
        "filename": generated_file.name,
        "fileUrl": generated_file.file.url,
        "prompt": prompt,
        "cost": str(usage["cost"]),
        "model": usage["model"],
        "size": usage["size"],
        "quality": usage["quality"],
    }
