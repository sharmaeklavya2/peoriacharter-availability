#!/usr/bin/env python
"""
Plans: declarative descriptions of what to check for.

A Plan is a pure function of the current date, producing the legs to look up.
It performs no I/O, so `plan.legs(some_date)` can be inspected for any date without touching the network.

Plans expire themselves: a plan only ever emits dates at or after `today`,
so a one-off trip stops generating work once the date has passed and needs no manual cleanup.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from typing import Callable, Final, Iterable, Iterator

from availability import resolve_dropoff, resolve_pickup


@dataclass(frozen=True)
class Leg:
    """One directed lookup: exactly the inputs `fetch_buses` takes.

    Frozen so legs are hashable and can be deduplicated across plans.
    """

    travel_date: Date
    source: str
    destination: str
    min_seats: int = 1

    def __str__(self) -> str:
        return (
            f"{self.travel_date:%a %Y-%m-%d} {self.source} -> {self.destination}"
        )


@dataclass(frozen=True)
class Plan:
    """A description of trips to watch."""

    name: str
    legs: Callable[[Date], Iterable[Leg]]
    enabled: bool = True


def weekend_trips(
    source: str,
    destination: str,
    *,
    weekends: int = 4,
    outbound_weekday: int = calendar.FRIDAY,
    return_weekday: int = calendar.SUNDAY,
    min_seats: int = 1,
) -> Callable[[Date], Iterator[Leg]]:
    """Round trips over the next `weekends` weekends, as pairs of one-way legs.

    Anchored on the next `outbound_weekday` at or after the run date, so a plan
    run mid-weekend looks ahead rather than at a Friday that has already gone.
    """

    def legs(today: Date) -> Iterator[Leg]:
        first_outbound: Date = _next_weekday(today, outbound_weekday)
        gap: int = (return_weekday - outbound_weekday) % 7
        for week in range(weekends):
            outbound: Date = first_outbound + timedelta(weeks=week)
            back: Date = outbound + timedelta(days=gap)
            yield Leg(outbound, source, destination, min_seats)
            yield Leg(back, destination, source, min_seats)

    return legs


def one_off(
    source: str,
    destination: str,
    *,
    on: Date,
    starting_days_before: int = 30,
    min_seats: int = 1,
) -> Callable[[Date], Iterator[Leg]]:
    """A single dated trip, checked only within the window before it.

    Emits nothing outside that window, so the plan goes quiet once the date has
    passed and can be left in the registry indefinitely.
    """

    def legs(today: Date) -> Iterator[Leg]:
        days_away: int = (on - today).days
        if 0 <= days_away <= starting_days_before:
            yield Leg(on, source, destination, min_seats)

    return legs


def _next_weekday(start: Date, weekday: int) -> Date:
    """The first date at or after `start` falling on `weekday` (Mon=0)."""
    return start + timedelta(days=(weekday - start.weekday()) % 7)


def collect_legs(plans: Iterable[Plan], today: Date) -> dict[Leg, set[str]]:
    """Expand every enabled plan into a deduplicated leg -> plan-names map.

    Two plans asking for the same leg should cost one request and produce one
    notification, while still reporting which plans wanted it.
    """
    wanted: dict[Leg, set[str]] = {}
    for plan in plans:
        if not plan.enabled:
            continue
        for leg in plan.legs(today):
            if leg.travel_date < today:
                continue  # defensive: never look up a date in the past
            wanted.setdefault(leg, set()).add(plan.name)
    return wanted


PLANS: Final[list[Plan]] = [
    Plan(
        name="purdue-weekends",
        legs=weekend_trips("ISR (UIUC)", "Lilly Hall", weekends=4),
    ),
    # Activate near the end of the semester by setting enabled=True and fixing the flight date.
    Plan(
        name="winter-flight-ohare",
        legs=one_off(
            "ISR (UIUC)",
            "O'Hare Terminal 2 Departures",
            on=Date(2026, 12, 19),
            starting_days_before=30,
        ),
        enabled=False,
    ),
]


if __name__ == "__main__":
    today: Date = Date.today()
    print(f"Plans for {today:%a %Y-%m-%d}:\n")
    for leg, names in sorted(
        collect_legs(PLANS, today).items(), key=lambda item: item[0].travel_date
    ):
        # Fail loudly here rather than at request time on a typo'd stop name.
        resolve_pickup(leg.source)
        resolve_dropoff(leg.destination)
        print(f"  {leg}   [{', '.join(sorted(names))}]")
