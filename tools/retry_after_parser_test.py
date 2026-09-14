"""Test-only audit for parsing Retry-After from generic HTTP/Cloudflare errors.

This file must not import or modify production downloader code. It validates the
expected retry-delay decision independently before any production change.
"""
from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return max(0.0, float(value))
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        base = now or datetime.now(timezone.utc)
        return max(0.0, (dt - base).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def main() -> int:
    print("[TEST] === ATTEMPT 17: Retry-After parser audit ===")

    cases = [
        ("120", 120.0),
        ("0", 0.0),
        ("15.5", 15.5),
        ("garbage", None),
    ]
    for raw, expected in cases:
        got = parse_retry_after(raw)
        ok = got == expected
        print(f"[TEST] numeric {raw!r}: parsed={got} expected={expected} {'PASS' if ok else 'FAIL'}")
        if not ok:
            return 1

    base = datetime(2026, 9, 14, 16, 25, 0, tzinfo=timezone.utc)
    http_date = "Mon, 14 Sep 2026 16:27:00 GMT"
    got = parse_retry_after(http_date, now=base)
    print(f"[TEST] HTTP-date Retry-After: parsed={got} expected=120.0 {'PASS' if got == 120.0 else 'FAIL'}")
    if got != 120.0:
        return 1

    print("[TEST] RESULT: PASS")
    print("[TEST] Expected production behavior: honor valid Retry-After instead of fixed 2s/4s retry gaps.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
