"""Permissions that refuse a request when an admin has switched a feature off."""

from rest_framework.permissions import BasePermission

from feature_flags.constants import ENABLE_OCR, ENABLE_RAG
from feature_flags.services import is_flag_enabled_for_user


class IsRagEnabled(BasePermission):
    message = "Document sources are turned off on this server."

    def has_permission(self, request, view):
        return is_flag_enabled_for_user(request.user, ENABLE_RAG)


class IsOcrEnabled(BasePermission):
    message = "Scanned-page transcription is turned off on this server."

    def has_permission(self, request, view):
        return is_flag_enabled_for_user(request.user, ENABLE_OCR)
