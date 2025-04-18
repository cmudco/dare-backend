"""Sentry setup and configuration"""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration


def init_sentry(*, dsn,):
    """
    Initializes sentry

    Args:
        dsn (str): the sentry DSN key
    """
    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            DjangoIntegration(),
        ],
        traces_sample_rate=1.0,
    )
