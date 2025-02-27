"""Sentry setup and configuration"""

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration


def init_sentry(*, dsn, environment):
    """
    Initializes sentry

    Args:
        dsn (str): the sentry DSN key
        environment (str): the application environment
    """
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        integrations=[
            DjangoIntegration(),
        ],
        traces_sample_rate=1.0,
    )
