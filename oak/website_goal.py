"""Bounded public task intent for website planning, never credential input."""

import re


DEFAULT_GOAL = 'Sign in to the existing account.'


def validate_task_goal(value):
    """Require a short objective without common private-value representations.

    This is structural validation, not a detector for arbitrary private facts.
    The caller must supply a generic objective and keep human values in the
    private form. The objective grants no execution or destination authority.
    """
    if type(value) is not str:
        raise ValueError('Use a short public task objective without private values.')
    try:
        valid_size = 0 < len(value.encode('utf-16-le')) // 2 <= 400 and len(value.encode('utf-8')) <= 1600
    except UnicodeError:
        valid_size = False
    if (not valid_size or not value.strip() or re.search(r'[\x00-\x1f\x7f]', value)
            or re.search(r'\S+@\S+|(?:https?://|www\.)\S+|\b[A-Za-z0-9_-]{24,}\b', value, re.I)):
        raise ValueError('Use a short public task objective without private values.')
    return value.strip()
