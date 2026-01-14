"""
URL configuration for MCP app.
"""

from django.urls import path, include

app_name = 'mcp'

urlpatterns = [
    path('api/', include('mcp.api.urls')),
]
