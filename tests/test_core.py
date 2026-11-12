"""Tests standard tap features using the built-in SDK tests library."""

from singer_sdk.testing import get_tap_test_class

from tap_beehiiv.tap import TapBeehiiv

SAMPLE_CONFIG = {
    "api_key": "test_api_key",
}


# Run standard built-in tap tests from the SDK:
TestTapBeehiiv = get_tap_test_class(
    tap_class=TapBeehiiv,
    config=SAMPLE_CONFIG,
)
