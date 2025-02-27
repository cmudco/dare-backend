from rest_framework.response import Response


class APIResponse(Response):
    """
    A wrapper around DRF's Response to ensure a consistent response format.
    """

    def __init__(self, status, message=None, data=None, errors=None, **kwargs):
        """
        Initialize the API response with consistent formatting.
        """
        formatted_data = {}

        if message:
            formatted_data["message"] = message

        if errors:
            formatted_data["errors"] = errors

        if data:
            formatted_data["data"] = data

        super().__init__(data=formatted_data, status=status, **kwargs)
