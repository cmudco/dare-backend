"""Content negotiation helpers for conversation API views."""

from rest_framework.negotiation import DefaultContentNegotiation


class ArtifactDownloadContentNegotiation(DefaultContentNegotiation):
    """Let artifact downloads use `?format=` as a business parameter."""

    def select_renderer(self, request, renderers, format_suffix=None):
        safe_request = _RequestWithoutFormatOverride(
            request,
            self.settings.URL_FORMAT_OVERRIDE,
        )
        return super().select_renderer(safe_request, renderers, format_suffix)


class _RequestWithoutFormatOverride:
    def __init__(self, request, format_key: str):
        self._request = request
        self.query_params = _QueryParamsWithoutFormatOverride(
            request.query_params,
            format_key,
        )

    def __getattr__(self, name: str):
        return getattr(self._request, name)


class _QueryParamsWithoutFormatOverride:
    def __init__(self, query_params, format_key: str):
        self._query_params = query_params
        self._format_key = format_key

    def get(self, key: str, default=None):
        if key == self._format_key:
            return default
        return self._query_params.get(key, default)
