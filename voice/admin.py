from django.contrib import admin
from voice.models import VoiceAgent, VoiceConversation


@admin.register(VoiceAgent)
class VoiceAgentAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'status', 'elevenlabs_agent_id', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['name', 'user__email', 'elevenlabs_agent_id']
    readonly_fields = ['elevenlabs_agent_id', 'created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(VoiceConversation)
class VoiceConversationAdmin(admin.ModelAdmin):
    list_display = ['elevenlabs_conversation_id', 'agent', 'user', 'status', 'started_at', 'duration_seconds']
    list_filter = ['status', 'started_at']
    search_fields = ['elevenlabs_conversation_id', 'user__email', 'agent__name']
    readonly_fields = ['elevenlabs_conversation_id', 'started_at', 'ended_at', 'duration_seconds', 'transcript']
    ordering = ['-started_at']
