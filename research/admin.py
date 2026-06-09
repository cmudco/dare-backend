from django.contrib import admin

from research.models import (
    ResearchAgentRun,
    ResearchAgentToolCall,
    ResearchMemoryProposal,
    ResearchProject,
    ResearchProjectMemory,
    ResearchSession,
    ResearchSource,
    SoulFile,
    SoulFileVersion,
)


@admin.register(ResearchProject)
class ResearchProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "user", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "question", "field")
    raw_id_fields = ("user",)


@admin.register(ResearchSession)
class ResearchSessionAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "user", "mode", "status", "created_at")
    list_filter = ("mode", "status")
    raw_id_fields = ("project", "user")


class ResearchAgentToolCallInline(admin.TabularInline):
    model = ResearchAgentToolCall
    extra = 0


@admin.register(ResearchAgentRun)
class ResearchAgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "role", "mode", "status", "created_at")
    list_filter = ("mode", "status", "role")
    search_fields = ("task",)
    raw_id_fields = ("session", "project", "user")
    inlines = [ResearchAgentToolCallInline]


@admin.register(ResearchAgentToolCall)
class ResearchAgentToolCallAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "tool", "status", "duration_ms", "created_at")
    list_filter = ("status", "tool")
    raw_id_fields = ("run",)


@admin.register(ResearchSource)
class ResearchSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "name", "kind", "source_type", "created_at")
    list_filter = ("source_type",)
    search_fields = ("name", "title", "authors")
    raw_id_fields = ("project", "added_by")


class SoulFileVersionInline(admin.TabularInline):
    model = SoulFileVersion
    extra = 0


@admin.register(SoulFile)
class SoulFileAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "name", "created_at")
    raw_id_fields = ("project",)
    inlines = [SoulFileVersionInline]


@admin.register(SoulFileVersion)
class SoulFileVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "soul_file", "version", "origin", "created_at")
    list_filter = ("origin",)
    raw_id_fields = ("soul_file", "created_by")


@admin.register(ResearchProjectMemory)
class ResearchProjectMemoryAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "label", "source", "created_at")
    list_filter = ("source",)
    search_fields = ("label", "detail")
    raw_id_fields = ("project", "added_by")


@admin.register(ResearchMemoryProposal)
class ResearchMemoryProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "proposed_by_role", "status", "created_at")
    list_filter = ("status", "memory_type")
    raw_id_fields = ("project", "run", "accepted_by")
