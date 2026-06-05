from django.contrib import admin

# isort: off
from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchKnowledgeItem,
    ResearchProject,
    ResearchSoulFile,
    ResearchSoulFileVersion,
    ResearchSource,
    ResearchStagingItem,
)

# isort: on


@admin.register(ResearchProject)
class ResearchProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "status", "is_deleted", "updated_at")
    list_filter = ("status", "is_deleted", "created_at")
    search_fields = ("title", "question", "field", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResearchSource)
class ResearchSourceAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "user", "kind", "is_deleted", "created_at")
    list_filter = ("kind", "is_deleted", "created_at")
    search_fields = ("title", "citation", "doi", "url", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResearchSoulFile)
class ResearchSoulFileAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "template_key", "is_default", "updated_at")
    list_filter = ("template_key", "is_default", "is_deleted", "created_at")
    search_fields = ("title", "description", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResearchSoulFileVersion)
class ResearchSoulFileVersionAdmin(admin.ModelAdmin):
    list_display = ("title", "soul_file", "version_number", "user", "created_at")
    list_filter = ("template_key", "created_at")
    search_fields = ("title", "body", "soul_file__title", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResearchStagingItem)
class ResearchStagingItemAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "user", "status", "created_at")
    list_filter = ("status", "evidence_label", "is_deleted", "created_at")
    search_fields = ("title", "rationale", "content", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResearchKnowledgeItem)
class ResearchKnowledgeItemAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "user", "approved_at")
    list_filter = ("evidence_label", "is_deleted", "approved_at")
    search_fields = ("title", "rationale", "content", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ResearchAgentRun)
class ResearchAgentRunAdmin(admin.ModelAdmin):
    list_display = ("run_id", "project", "user", "role", "status", "created_at")
    list_filter = ("role", "status", "output_destination", "is_deleted", "created_at")
    search_fields = ("run_id", "task", "status_message", "error_message", "user__email")
    readonly_fields = ("run_id", "created_at", "updated_at")


@admin.register(ResearchAgentToolCall)
class ResearchAgentToolCallAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "run", "project", "user", "status", "created_at")
    list_filter = ("tool_name", "status", "created_at")
    search_fields = (
        "tool_name",
        "input_summary",
        "output_summary",
        "error_message",
        "user__email",
    )
    readonly_fields = ("created_at", "updated_at")
