"""Test package for default unittest discovery."""

from tests.fixture_clock import pin_freshness_clock

# Fixture releases carry real review due dates; evaluate freshness at a fixed time
# so the suites (and the browser fixture server) do not expire (#64).
pin_freshness_clock()
