from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Workflow,
    # Graph-driven models
    WorkflowNode, WorkflowEdge,
    StepNodeData, StartNodeData, ChatOutputNodeData,
    # Execution models
    BatchRun, WorkflowRun, WorkflowRunStep,
)

class WorkflowNodeInline(admin.TabularInline):
    model = WorkflowNode
    extra = 0
    fields = ('node_id', 'node_type', 'position_x', 'position_y')
    readonly_fields = ('data',)

class WorkflowEdgeInline(admin.TabularInline):
    model = WorkflowEdge
    extra = 0
    fields = ('edge_id', 'source', 'target', 'edge_type')

@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "mode", "is_active", "created_at")
    search_fields = ("user__email",)
    list_filter = ("is_active", "created_at")
    ordering = ("-created_at",)
    inlines = [WorkflowNodeInline, WorkflowEdgeInline]
    readonly_fields = ('title', 'description', 'mode')  # These come from StartNodeData

# New node data admins
@admin.register(WorkflowNode)
class WorkflowNodeAdmin(admin.ModelAdmin):
    list_display = ('workflow', 'node_id', 'node_type', 'position_x', 'position_y')
    list_filter = ('node_type', 'workflow')
    search_fields = ('node_id', 'workflow__user__email')

@admin.register(WorkflowEdge)
class WorkflowEdgeAdmin(admin.ModelAdmin):
    list_display = ('workflow', 'edge_id', 'source', 'target', 'edge_type')
    list_filter = ('edge_type', 'workflow')
    search_fields = ('edge_id', 'source', 'target')

@admin.register(StepNodeData)
class StepNodeDataAdmin(admin.ModelAdmin):
    list_display = ('id', 'prompt', 'llm', 'temperature')
    list_filter = ('llm', 'created_at')
    search_fields = ('prompt__title',)

@admin.register(StartNodeData)
class StartNodeDataAdmin(admin.ModelAdmin):
    list_display = ('title', 'mode', 'created_at')
    list_filter = ('mode', 'created_at')
    search_fields = ('title', 'description')

@admin.register(ChatOutputNodeData)
class ChatOutputNodeDataAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'created_at')
    list_filter = ('status', 'created_at')


# ==================== Execution Models ====================

class WorkflowRunStepInline(admin.TabularInline):
    model = WorkflowRunStep
    extra = 0
    fields = ('order', 'step_node', 'status', 'error', 'started_at', 'created_at')
    readonly_fields = ('order', 'step_node', 'status', 'error', 'started_at', 'created_at')
    ordering = ('order',)

    def has_add_permission(self, request, obj=None):
        return False


class WorkflowRunInline(admin.TabularInline):
    model = WorkflowRun
    extra = 0
    fields = ('id', 'status', 'batch_file', 'created_at', 'ended_at')
    readonly_fields = ('id', 'status', 'batch_file', 'created_at', 'ended_at')

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BatchRun)
class BatchRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'workflow', 'user', 'status', 'total_files', 'completed_count', 'failed_count', 'created_at', 'ended_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'workflow__title')
    ordering = ('-created_at',)
    readonly_fields = ('workflow', 'user', 'status', 'total_files', 'completed_count', 'failed_count', 'created_at', 'ended_at')
    inlines = [WorkflowRunInline]


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ('id', 'workflow', 'user', 'status', 'batch_run', 'batch_file', 'created_at', 'ended_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'workflow__title')
    ordering = ('-created_at',)
    readonly_fields = ('workflow', 'user', 'status', 'batch_run', 'batch_file', 'is_partial', 'created_at', 'ended_at')
    inlines = [WorkflowRunStepInline]


@admin.register(WorkflowRunStep)
class WorkflowRunStepAdmin(admin.ModelAdmin):
    list_display = ('id', 'workflow_run', 'order', 'step_node', 'status', 'has_error', 'started_at', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('workflow_run__workflow__title', 'workflow_run__user__email', 'error')
    ordering = ('-created_at', 'order')
    readonly_fields = ('workflow_run', 'step_node', 'order', 'status', 'response', 'error', 'metadata', 'started_at', 'created_at')

    @admin.display(boolean=True, description='Error?')
    def has_error(self, obj):
        return bool(obj.error)
