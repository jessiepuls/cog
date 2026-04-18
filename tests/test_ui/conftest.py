"""pytest configuration for test_ui — suppresses ResourceWarning from Textual's
driver sockets, which are an upstream issue in Textual's test infrastructure."""

import warnings

import pytest


@pytest.fixture(autouse=True)
def _suppress_textual_resource_warnings():
    # Textual's headless test driver creates Unix domain sockets that may not
    # be closed before Python GC runs.  This is a Textual internal issue, not
    # in our code.  Suppress the ResourceWarning so it doesn't fail our suite.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        yield
