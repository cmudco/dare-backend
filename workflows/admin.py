from django.contrib import admin
from .models import Workflow, Step

class StepInline(admin.TabularInline):
    model = Workflow.steps.through
    extra = 1

@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "mode", "is_active", "created_at")
    search_fields = ("title", "description", "user__email")
    list_filter = ("mode", "is_active", "created_at")
    ordering = ("-created_at",)
    inlines = [StepInline]
    exclude = ('steps',)

@admin.register(Step)
class StepAdmin(admin.ModelAdmin):
    list_display = ("prompt", "order", "user")
    search_fields = ("prompt__title", "user__email")
    list_filter = ("created_at",)
    ordering = ("order",)
