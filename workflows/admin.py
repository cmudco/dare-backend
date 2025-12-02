from django.contrib import admin
from .models import (
    Workflow,
    # Graph-driven models
    WorkflowNode, WorkflowEdge,
    StepNodeData, StartNodeData, ChatOutputNodeData
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
    list_display = ('step_number', 'prompt', 'llm', 'temperature')
    list_filter = ('llm', 'created_at')
    search_fields = ('prompt__title',)

@admin.register(StartNodeData)
class StartNodeDataAdmin(admin.ModelAdmin):
    list_display = ('title', 'mode', 'created_at')
    list_filter = ('mode', 'created_at')
    search_fields = ('title', 'description')

@admin.register(ChatOutputNodeData)
class ChatOutputNodeDataAdmin(admin.ModelAdmin):
    list_display = ('step_number', 'status', 'created_at')
    list_filter = ('status', 'created_at')
