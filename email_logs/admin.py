# email_logs/admin.py
from django.contrib import admin
from .models import EmailLog

@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'subject', 'status', 'sent_at', 'created_at')
    list_filter = ('status', 'sent_at')
    search_fields = ('recipient', 'subject')
    readonly_fields = ('created_at', 'updated_at', 'sent_at', 'error_message')