from django.contrib import admin
from agents.models import Agent, AgentNodeData, TemplateAgentNodeData


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'version', 'created_at')
    list_filter = ('user', 'created_at', 'enable_web_search')
    search_fields = ('name', 'description', 'user__email')
    readonly_fields = ('created_at', 'updated_at', 'version')
    filter_horizontal = ('content_files', 'embedding_files')

    fieldsets = (
        ('Basic Information', {
            'fields': ('user', 'name', 'description')
        }),
        ('Configuration', {
            'fields': ('prompt', 'llm')
        }),
        ('Files', {
            'fields': ('content_files', 'embedding_files')
        }),
        ('LLM Settings', {
            'fields': (
                'max_tokens',
                'temperature',
                'max_context_snippets',
                'document_similarity_threshold',
                'enable_web_search'
            )
        }),
        ('Versioning', {
            'fields': ('version', 'parent')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(AgentNodeData)
class AgentNodeDataAdmin(admin.ModelAdmin):
    list_display = ('agent_number', 'name', 'agent', 'created_at')
    list_filter = ('agent_number', 'created_at', 'enable_web_search')
    search_fields = ('name', 'description', 'agent__name')
    readonly_fields = ('created_at', 'updated_at')
    filter_horizontal = ('content_files', 'embedding_files')

    fieldsets = (
        ('Basic Information', {
            'fields': ('agent', 'name', 'description', 'agent_number')
        }),
        ('Configuration', {
            'fields': ('prompt', 'llm')
        }),
        ('Files', {
            'fields': ('content_files', 'embedding_files')
        }),
        ('LLM Settings', {
            'fields': (
                'max_tokens',
                'temperature',
                'max_context_snippets',
                'document_similarity_threshold',
                'enable_web_search'
            )
        }),
        ('Options', {
            'fields': (
                'use_previous_agent_files',
                'use_previous_agent_embeddings',
                'text_input',
                'use_structured_output_node'
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(TemplateAgentNodeData)
class TemplateAgentNodeDataAdmin(admin.ModelAdmin):
    list_display = ('agent_number', 'agent', 'name', 'created_at')
    list_filter = ('agent_number', 'created_at')
    search_fields = ('name', 'description', 'agent__name')
    readonly_fields = ('created_at', 'updated_at')

    fieldsets = (
        ('Basic Information', {
            'fields': ('agent', 'name', 'description', 'agent_number')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
