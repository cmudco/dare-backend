from django.contrib import admin

from assistant.models import AssistantKnowledgeSource, AssistantMessage, AssistantThread


@admin.register(AssistantKnowledgeSource)
class AssistantKnowledgeSourceAdmin(admin.ModelAdmin):
    list_display = ("id", "label", "file", "file_owner", "file_status", "is_active")
    list_filter = ("is_active",)
    raw_id_fields = ("file",)

    @admin.display(description="Owner")
    def file_owner(self, obj):
        return obj.file.user

    @admin.display(description="Ingestion")
    def file_status(self, obj):
        return obj.file.get_status_display()


class AssistantMessageInline(admin.TabularInline):
    model = AssistantMessage
    extra = 0
    fields = ("role", "status", "content", "page_path", "input_tokens", "output_tokens")
    readonly_fields = fields
    can_delete = False


@admin.register(AssistantThread)
class AssistantThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at", "closed_at")
    raw_id_fields = ("user",)
    inlines = [AssistantMessageInline]
