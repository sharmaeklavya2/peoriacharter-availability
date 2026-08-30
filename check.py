#!/usr/bin/env python
"""
Run every enabled plan and print the buses found for each leg.

One leg failing does not abandon the run: the failure is reported in place and
the remaining legs are still checked. The exit status is non-zero if any leg failed,
so a caller can distinguish 'nothing available' from a partly broken run.
"""

from __future__ import annotations

import sys
import time
from datetime import date as Date
from typing import Final, Iterable, Sequence

from availability import AvailabilityError, Bus, fetch_buses
from plans import PLANS, Leg, Plan, collect_legs

REQUEST_SPACING: Final[float] = 1.0


def check_plans(plans: Iterable[Plan], today: Date) -> int:
    """Print results for every leg of every enabled plan; return a failure count."""
    wanted: dict[Leg, set[str]] = collect_legs(plans, today)
    if not wanted:
        print("No legs to check today.")
        return 0

    legs: list[Leg] = sorted(wanted, key=lambda leg: (leg.travel_date, leg.source))
    print(f"Checking {len(legs)} legs for {today:%a %Y-%m-%d}\n")

    failures: int = 0
    for index, leg in enumerate(legs):
        if index:
            time.sleep(REQUEST_SPACING)
        print(f"{leg}   [{', '.join(sorted(wanted[leg]))}]")
        try:
            buses: Sequence[Bus] = fetch_buses(
                leg.travel_date,
                leg.source,
                leg.destination,
                min_seats=leg.min_seats,
            )
        except AvailabilityError as exc:
            # Distinct from 'no buses': the question went unanswered.
            failures += 1
            print(f"    ! {type(exc).__name__}: {exc}")
        else:
            if buses:
                for bus in buses:
                    print(f"    {bus}")
            else:
                print("    (nothing available)")
        print()
    return failures


def main() -> int:
    failures: int = check_plans(PLANS, Date.today())
    if failures:
        print(f"{failures} leg(s) could not be checked.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
