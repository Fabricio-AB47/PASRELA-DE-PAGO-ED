#!/usr/bin/env python
"""Wrapper to run the Django project from the backend root."""
import os
import sys
from pathlib import Path


def main():
    backend_root = Path(__file__).resolve().parent
    project_root = backend_root / 'E_Cont'

    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    os.chdir(project_root)
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'E_Cont.settings')

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
