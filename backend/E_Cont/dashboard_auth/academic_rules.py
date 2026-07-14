from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


MINIMUM_PASSING_GRADE = Decimal('7.00')
MAXIMUM_GRADE = Decimal('10.00')


def is_passing_grade(value: Any) -> bool:
    """Indica si una nota está dentro del rango aprobatorio institucional."""
    if value is None or value == '':
        return False
    try:
        grade = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return MINIMUM_PASSING_GRADE <= grade <= MAXIMUM_GRADE
