"""Local-only exclusion list for the sort-report pipeline.

PII / personal strings live in the BIDIQ_EXCLUSION_STRINGS env var
(pipe-delimited), never in git. Files whose name or first-pages text
contains any of these strings are quarantined to 99-EXCLUDED-PERSONAL/
before classification, so they never hit the Claude API.

See README.md "Sort Report Mode" for setup details.
"""
from __future__ import annotations

import os

EXCLUSION_STRINGS: list[str] = [
    s.strip()
    for s in os.environ.get("BIDIQ_EXCLUSION_STRINGS", "").split("|")
    if s.strip()
]


if __name__ == "__main__":
    if EXCLUSION_STRINGS:
        print(f"Loaded {len(EXCLUSION_STRINGS)} exclusion string(s) from env.")
    else:
        print(
            "WARNING: BIDIQ_EXCLUSION_STRINGS is empty. "
            "No exclusions will be applied."
        )
