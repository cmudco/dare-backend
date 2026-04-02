from __future__ import annotations

from typing import Any

import requests

from syftbox.constants import REQUEST_TIMEOUT
from syftbox.errors import SyftBoxException, SyftBoxErrorCode


class HttpClient:
    """Small HTTP client for SyftBox requests."""

    def request(
        self,
        method: str,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        request_headers = headers.copy() if headers else {}
        if access_token:
            request_headers["Authorization"] = f"Bearer {access_token}"

        request_kwargs: dict[str, Any] = {"headers": request_headers, "timeout": REQUEST_TIMEOUT}
        if files:
            request_kwargs["files"] = files
        elif data is not None:
            request_kwargs["json"] = data

        try:
            response = requests.request(method=method, url=url, **request_kwargs)
        except requests.Timeout as error:
            raise SyftBoxException(
                SyftBoxErrorCode.TIMEOUT,
                "SyftBox request timed out",
                cause=error,
            )
        except requests.RequestException as error:
            raise SyftBoxException(
                SyftBoxErrorCode.NETWORK_ERROR,
                "Network error while calling SyftBox",
                cause=error,
            )

        payload: Any
        try:
            payload = response.json()
        except Exception:
            payload = {"error": response.text}

        if isinstance(payload, dict):
            payload["status_code"] = response.status_code

        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else "Request failed"
            raise SyftBoxException(
                SyftBoxErrorCode.UNKNOWN_ERROR,
                message or "Request failed",
                details=payload,
            )

        if isinstance(payload, dict):
            return payload
        return {"data": payload, "status_code": response.status_code}

    def post(
        self,
        url: str,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, Any] | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        return self.request(
            method="POST",
            url=url,
            data=data,
            headers=headers,
            files=files,
            access_token=access_token,
        )
