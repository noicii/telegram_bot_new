"""Test-only simulation of generic retry-delay selection.

No production imports or modifications. Verifies that a valid Retry-After
value overrides the normal short exponential retry delay, while ordinary
errors retain the normal policy.
"""
from __future__ import annotations


def choose_retry_delay(*, retry_after: float | None, retry_index: int) -> float:
    normal = min(2.0 * (retry_index + 1), 5.0)
    if retry_after is not None and retry_after >= 0:
        return retry_after
    return normal


def main() -> int:
    print("[TEST] === ATTEMPT 18: Retry-After policy simulation ===")

    cases = [
        (120.0, 0, 120.0, "522 with Retry-After"),
        (120.0, 1, 120.0, "522 retry #2 with Retry-After"),
        (None, 0, 2.0, "ordinary error retry #1"),
        (None, 1, 4.0, "ordinary error retry #2"),
        (None, 2, 5.0, "ordinary error retry #3"),
        (0.0, 0, 0.0, "explicit zero Retry-After"),
    ]

    for retry_after, retry_index, expected, label in cases:
        got = choose_retry_delay(retry_after=retry_after, retry_index=retry_index)
        ok = got == expected
        print(f"[TEST] {label}: delay={got}s expected={expected}s {'PASS' if ok else 'FAIL'}")
        if not ok:
            print("[TEST] RESULT: FAIL")
            return 1

    print("[TEST] RESULT: PASS")
    print("[TEST] Policy proven: valid Retry-After takes precedence; ordinary errors keep 2s/4s/5s backoff.")
    print("[TEST] Production downloader files were not imported or modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
