"""Harness constants. Protected: changes need the lead's approval.

Holdout (SPEC 6.2 #10): the 12 months before the start of the project are sealed.
Proposal pending lead decision: a fixed window, not a rolling one. Data after HOLDOUT_END
is forward data (paper trading, phase B forward evaluation), never used for development.
"""

from datetime import datetime, timezone

HOLDOUT_START = datetime(2025, 9, 24, tzinfo=timezone.utc)
HOLDOUT_END = datetime(2026, 9, 24, tzinfo=timezone.utc)
# Development data ends at a month boundary before the holdout. The gap is an embargo.
DEV_END = datetime(2025, 9, 1, tzinfo=timezone.utc)

# Regimes for robustness splits (SPEC 6.2 #8), clipped to development data.
REGIMES = {
    "2020-21 bull": ("2020-01-01", "2021-11-10"),
    "2022 crash": ("2021-11-10", "2022-12-31"),
    "2023-24 recovery": ("2023-01-01", "2024-12-31"),
    "2025": ("2025-01-01", "2025-09-01"),
}

# Crash stress windows inside development data (SPEC 6.2 #9). 10 Oct 2025 is in the holdout.
CRASHES = {
    "Mar 2020": ("2020-03-08", "2020-03-20"),
    "May 2021": ("2021-05-10", "2021-05-25"),
    "Nov 2022 FTX": ("2022-11-05", "2022-11-15"),
}

TRIAL_BUDGET_DEFAULT = 50
