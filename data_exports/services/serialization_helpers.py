import json
from datetime import date, datetime
from typing import Any

from djangorestframework_camel_case.util import camelize


def to_iso(value: Any) -> str | None:
    """Return an ISO string for date-like values."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def json_dumps(data: Any) -> str:
    """Serialize export JSON with stable formatting and date/decimal fallbacks."""
    return json.dumps(
        camelize(data), ensure_ascii=False, indent=2, sort_keys=True, default=str
    )
