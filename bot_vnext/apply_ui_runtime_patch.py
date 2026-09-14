#!/usr/bin/env python3
"""Compatibility shim for older VM updater scripts.

The current V2 code no longer needs runtime UI monkey patches. Older copies of
update.sh may still invoke this path after syncing a newer revision, so keep a
safe no-op entry point until those updater copies are refreshed.
"""

if __name__ == "__main__":
    print("UI runtime compatibility patch: not required for current Bot V2")
