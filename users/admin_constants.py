LAST_LOGIN_DISPLAY_RULES = (
    (0, "green", "Today"),
    (7, "green", "{days}d ago"),
    (30, "orange", "{days}d ago"),
    (90, "darkorange", "{days}d ago"),
)

LAST_LOGIN_DISPLAY_DEFAULT = ("red", "{days}d ago")

ACTIVITY_LEVELS = (
    {"days": 7, "color": "#16a34a", "emoji": "🟢", "label": "ACTIVE"},
    {"days": 30, "color": "#ca8a04", "emoji": "🟡", "label": "MODERATE"},
    {"days": 90, "color": "#ea580c", "emoji": "🟠", "label": "LOW"},
    {"days": float("inf"), "color": "#dc2626", "emoji": "🔴", "label": "INACTIVE"},
)

ACTIVITY_NEVER_STATE = {"color": "#dc2626", "emoji": "🔴", "label": "NEVER"}
