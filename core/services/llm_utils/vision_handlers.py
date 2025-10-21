"""
Vision/multimodal content handling utilities for LLM providers.

This module provides functions to add image and video content to messages in the format
required by different LLM providers.

Provider Video Support:
- Claude: Images only (no video support)
- OpenAI: Video via frame extraction → images
- Gemini: Full native video support
"""

from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)


class VisionHandler:
    """Base vision handling utilities."""

    @staticmethod
    def find_last_user_message_index(messages: List[Dict]) -> int:
        """
        Find the index of the last user message in the message list.

        Args:
            messages: List of message dictionaries

        Returns:
            Index of last user message, or -1 if not found
        """
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                return i
        return -1

    @staticmethod
    def separate_images_and_videos(media_items: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """
        Separate media items into images and videos based on MIME type.

        Args:
            media_items: List of media dictionaries with 'type' (MIME type)

        Returns:
            Tuple of (images, videos)
        """
        images = []
        videos = []

        for item in media_items:
            mime_type = item.get('type', '')
            if mime_type.startswith('video/'):
                videos.append(item)
            elif mime_type.startswith('image/'):
                images.append(item)
            else:
                # Default to image for unknown types
                images.append(item)

        return images, videos


class OpenAIVisionHandler:
    """OpenAI-specific vision content handling.

    OpenAI supports video through frame extraction - videos are converted to
    a sequence of image frames and sent as images.
    """

    @staticmethod
    def add_images_to_messages(messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content (images and videos) to the last user message in OpenAI format.

        OpenAI expects: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}

        For videos: Extracts frames and sends them as images.

        Args:
            messages: List of message dictionaries
            images: List of media dictionaries with 'preview' (base64 URL), 'type' (MIME)

        Returns:
            Modified messages list with media added
        """
        idx = VisionHandler.find_last_user_message_index(messages)
        if idx == -1:
            return messages

        # Separate images and videos
        image_items, video_items = VisionHandler.separate_images_and_videos(images)

        text_content = messages[idx]["content"]
        content_parts = [{"type": "text", "text": text_content}]

        # Add images directly
        for img in image_items:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": img["preview"]}
            })

        # Process videos: extract frames and add as images
        if video_items:
            try:
                video_frames = OpenAIVisionHandler._extract_video_frames(video_items)
                for frame in video_frames:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": frame["preview"]}
                    })

                # Add note about video processing
                if video_frames:
                    video_note = f"\n\n[Note: {len(video_items)} video(s) processed - showing {len(video_frames)} frames. Use the video transcription for audio content.]"
                    content_parts[0]["text"] += video_note

            except Exception as e:
                logger.error(f"Error extracting video frames for OpenAI: {str(e)}")
                # Add error note to message
                content_parts[0]["text"] += f"\n\n[Note: Could not process {len(video_items)} video(s) - video analysis not available]"

        messages[idx]["content"] = content_parts
        return messages

    @staticmethod
    def _extract_video_frames(videos: List[Dict], fps: float = 1.0, max_frames: int = 10) -> List[Dict]:
        """
        Extract frames from video base64 data.

        Args:
            videos: List of video dictionaries with 'preview' (base64 data URL)
            fps: Frames per second to extract (default 1.0)
            max_frames: Maximum number of frames to extract per video

        Returns:
            List of frame dictionaries with 'preview' (base64 data URL)
        """
        try:
            import cv2
            import numpy as np
            import base64
            import io
            from PIL import Image
        except ImportError:
            logger.error("OpenCV or PIL not installed - cannot extract video frames")
            return []

        frames = []

        for video in videos:
            try:
                # Extract base64 data from data URL
                preview = video.get('preview', '')
                if ',' in preview:
                    base64_data = preview.split(',')[1]
                else:
                    base64_data = preview

                # Decode base64 to bytes
                video_bytes = base64.b64decode(base64_data)

                # Write to temporary file (OpenCV needs file path)
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
                    tmp_file.write(video_bytes)
                    tmp_file_path = tmp_file.name

                # Open video with OpenCV
                cap = cv2.VideoCapture(tmp_file_path)
                video_fps = cap.get(cv2.CAP_PROP_FPS)
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

                # Calculate frame interval
                frame_interval = max(1, int(video_fps / fps))
                frames_to_extract = min(max_frames, total_frames // frame_interval)

                frame_count = 0
                extracted_count = 0

                while cap.isOpened() and extracted_count < frames_to_extract:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    # Extract frame at intervals
                    if frame_count % frame_interval == 0:
                        # Convert BGR to RGB
                        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                        # Convert to PIL Image
                        pil_image = Image.fromarray(frame_rgb)

                        # Convert to base64
                        buffer = io.BytesIO()
                        pil_image.save(buffer, format='JPEG', quality=85)
                        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

                        # Create data URL
                        frame_data_url = f"data:image/jpeg;base64,{img_base64}"

                        frames.append({
                            'preview': frame_data_url,
                            'name': f"frame_{extracted_count}",
                            'type': 'image/jpeg'
                        })

                        extracted_count += 1

                    frame_count += 1

                cap.release()

                # Clean up temp file
                import os
                os.unlink(tmp_file_path)

            except Exception as e:
                logger.error(f"Error extracting frames from video: {str(e)}")
                continue

        return frames


class ClaudeVisionHandler:
    """Claude-specific vision content handling.

    Claude ONLY supports images - no video support.
    Videos are filtered out with a warning message.
    """

    @staticmethod
    def add_images_to_messages(messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content to the last user message in Claude format.

        Claude expects: {"type": "image", "source": {"type": "base64", "media_type": "...", "data": "..."}}
        Note: Claude requires base64 WITHOUT the data URL prefix and ONLY supports images.

        Videos are filtered out as Claude does not support video input.

        Args:
            messages: List of message dictionaries
            images: List of media dictionaries with 'preview', 'type'

        Returns:
            Modified messages list with images added (videos excluded)
        """
        idx = VisionHandler.find_last_user_message_index(messages)
        if idx == -1:
            return messages

        # Separate images and videos - Claude only supports images
        image_items, video_items = VisionHandler.separate_images_and_videos(images)

        text_content = messages[idx]["content"]
        content_parts = [{"type": "text", "text": text_content}]

        # Add images only (Claude supports: image/jpeg, image/png, image/gif, image/webp)
        for img in image_items:
            # Validate MIME type for Claude
            mime_type = img.get("type", "")
            if mime_type in ['image/jpeg', 'image/png', 'image/gif', 'image/webp']:
                content_parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": ClaudeVisionHandler._extract_base64_data(img["preview"])
                    }
                })
            else:
                logger.warning(f"Unsupported image type for Claude: {mime_type}")

        # Add warning if videos were present
        if video_items:
            video_warning = f"\n\n[Note: {len(video_items)} video(s) excluded - Claude does not support video input. Please use OpenAI or Gemini for video analysis.]"
            content_parts[0]["text"] += video_warning
            logger.info(f"Filtered out {len(video_items)} video(s) for Claude (not supported)")

        messages[idx]["content"] = content_parts
        return messages

    @staticmethod
    def _extract_base64_data(preview: str) -> str:
        """
        Extract base64 data from data URL.

        Args:
            preview: Data URL or base64 string

        Returns:
            Pure base64 string without prefix
        """
        if "," in preview:
            return preview.split(",")[1]
        return preview


class GeminiVisionHandler:
    """Gemini-specific vision content handling.

    Gemini has FULL native support for both images and videos via inline data.
    """

    @staticmethod
    def add_images_to_messages(messages: List[Dict], images: List[Dict]) -> List[Dict]:
        """
        Add vision content (images and videos) to the last user message in Gemini format.

        Gemini supports both images and videos natively via inline_data with base64.
        Format: {"inline_data": {"mime_type": "video/mp4", "data": "base64..."}}

        Args:
            messages: List of message dictionaries
            images: List of media dictionaries with 'preview' (data URL), 'type' (MIME)

        Returns:
            Modified messages list with media added
        """
        idx = VisionHandler.find_last_user_message_index(messages)
        if idx == -1:
            return messages

        # Separate images and videos (though Gemini supports both)
        image_items, video_items = VisionHandler.separate_images_and_videos(images)

        text_content = messages[idx]["content"]
        messages[idx]["content"] = [
            {"type": "text", "text": text_content},
            *[
                {
                    "type": "image_url",
                    "image_url": {"url": img["preview"]}
                }
                for img in image_items
            ],
            *[
                {
                    "type": "image_url",  # Gemini uses same structure for videos
                    "image_url": {"url": video["preview"]}
                }
                for video in video_items
            ]
        ]

        # Add note if videos are included
        if video_items:
            video_note = f"\n\n[Note: {len(video_items)} video(s) included for analysis]"
            messages[idx]["content"][0]["text"] += video_note
            logger.info(f"Added {len(video_items)} video(s) for Gemini processing")

        return messages
