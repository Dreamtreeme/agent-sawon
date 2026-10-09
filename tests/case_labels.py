"""Failure labels for consolidated test-case loops."""

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def labeled_case(label: object) -> Iterator[None]:
    """Preserve the original failure while adding the current loop case to CI output."""
    try:
        yield
    except BaseException as error:
        if not isinstance(error, (KeyboardInterrupt, SystemExit)):
            error.add_note(f"consolidated-case={label}")
        raise
