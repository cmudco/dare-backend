"""
Media Helpers Module

Functions for processing images, files, and media content.
Handles image generation responses and file processing.
"""

from typing import Dict, Any


def build_generated_image_data(
    generated_file,
    prompt: str,
    usage: Dict[str, Any]
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
        "revisedPrompt": usage.get("revised_prompt", ""),
        "cost": str(usage.get("cost", "0.040")),
        "model": usage.get("model", "dall-e-3"),
        "size": usage.get("size", "1024x1024"),
        "quality": usage.get("quality", "standard"),
        "style": usage.get("style", "vivid"),
    }
