"""Credentials the test suite uses. conftest sets SPORTS_PICKS_OWNER_KEY to
TEST_OWNER_KEY for every test; a test that calls a guarded route sends these."""
TEST_OWNER_KEY = "test-owner-key"
OWNER_HEADERS = {"X-Owner-Key": TEST_OWNER_KEY}
TEST_PIN = "1234"
PIN_HEADERS = {"X-Player-Pin": TEST_PIN}
ALL_HEADERS = {**OWNER_HEADERS, **PIN_HEADERS}
