#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

from config import env


def main():
    """Run administrative tasks."""
    # constant from env used here instead of env variable key since it wasn't working for python commands.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", env.DJANGO_SETTINGS_MODULE or "config.settings.production")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
