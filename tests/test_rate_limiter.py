"""Tests for the rate limiter service."""

from __future__ import annotations

import time
from unittest.mock import patch

from tg_assistant.services.rate_limiter import RateLimiter


def test_under_limit():
    limiter = RateLimiter(max_per_minute=5)
    for _ in range(5):
        assert limiter.check(1) is True


def test_over_limit():
    limiter = RateLimiter(max_per_minute=3)
    assert limiter.check(1) is True
    assert limiter.check(1) is True
    assert limiter.check(1) is True
    assert limiter.check(1) is False


def test_different_users_independent():
    limiter = RateLimiter(max_per_minute=2)
    assert limiter.check(1) is True
    assert limiter.check(1) is True
    assert limiter.check(1) is False
    # User 2 still has their own window
    assert limiter.check(2) is True
    assert limiter.check(2) is True
    assert limiter.check(2) is False


def test_window_expires():
    limiter = RateLimiter(max_per_minute=2)
    assert limiter.check(1) is True
    assert limiter.check(1) is True
    assert limiter.check(1) is False

    # Fast-forward time by 61 seconds
    with patch.object(time, "monotonic", return_value=time.monotonic() + 61):
        assert limiter.check(1) is True


def test_zero_limit():
    limiter = RateLimiter(max_per_minute=0)
    assert limiter.check(1) is False
