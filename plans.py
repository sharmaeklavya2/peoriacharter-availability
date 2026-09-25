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
import csv
from dataclasses import dataclass
from datetime import date as Date
from datetime import timedelta
from pathlib import Path
from typing import Callable, Collection, Final, Iterable, Iterator

from availability import resolve_dropoff, resolve_pickup

# Bookings are personal state that changes weekly, so they live in a gitignored
# data file rather than in this module.
BOOKED_PATH: Final[Path] = Path(__file__).parent / "booked.csv"


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


@dataclass(frozen=True)
class BookedEntry:
    """A trip already ticketed: stop checking this plan on this date."""

    travel_date: Date
    plan: str


class BookedFileError(Exception):
    """The booked file could not be parsed.

    Raised rather than skipping bad lines: a typo that silently stops
    suppressing -- or silently suppresses the wrong thing -- is a failure you
    would never notice.
    """


def load_booked(path: Path, plans: Iterable[Plan]) -> set[BookedEntry]:
    """Read booked trips from a CSV of `date,plan` rows.

    Blank lines, `#` comments and an optional `date,plan` header are ignored. A
    missing file means nothing is booked, so a fresh clone just works.

    Plan names are validated against `plans`, which catches both typos and
    entries orphaned by renaming a plan.
    """
    try:
        text: str = path.read_text()
    except FileNotFoundError:
        return set()

    known: set[str] = {plan.name for plan in plans}
    booked: set[BookedEntry] = set()
    for number, line in enumerate(text.splitlines(), start=1):
        stripped: str = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        row: list[str] = [x.strip() for x in next(csv.reader([stripped]))]
        if len(row) not in (2, 3):
            raise BookedFileError(
                f"{path}:{number}: expected 'date,plan[,status]', got {stripped!r}"
            )
        if row[0].lower() == 'date' and row[1].lower() == 'plan':
            continue
        raw_date, raw_plan = row[0], row[1]
        try:
            travel_date: Date = Date.fromisoformat(raw_date)
        except ValueError:
            raise BookedFileError(
                f"{path}:{number}: {raw_date!r} is not an ISO date (YYYY-MM-DD)"
            ) from None
        if raw_plan not in known:
            raise BookedFileError(
                f"{path}:{number}: unknown plan {raw_plan!r}; known: {sorted(known)}"
            )
        booked.add(BookedEntry(travel_date, raw_plan))
    return booked


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


def collect_legs(
    plans: Iterable[Plan],
    today: Date,
    booked: Collection[BookedEntry] = (),
) -> dict[Leg, set[str]]:
    """Expand every enabled plan into a deduplicated leg -> plan-names map.

    Two plans asking for the same leg should cost one request and produce one
    notification, while still reporting which plans wanted it.

    Booked trips are dropped per plan, not per leg: if two plans want the same
    leg and only one has it booked, the leg is still checked for the other and
    the attribution stays honest.

    Pure -- callers pass `booked` in rather than this reading the file.
    """
    already: set[BookedEntry] = set(booked)
    wanted: dict[Leg, set[str]] = {}
    for plan in plans:
        if not plan.enabled:
            continue
        for leg in plan.legs(today):
            if leg.travel_date < today:
                continue  # defensive: never look up a date in the past
            if BookedEntry(leg.travel_date, plan.name) in already:
                continue
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
    booked: set[BookedEntry] = load_booked(BOOKED_PATH, PLANS)
    print(f"Plans for {today:%a %Y-%m-%d} ({len(booked)} booked):\n")
    for leg, names in sorted(
        collect_legs(PLANS, today, booked).items(),
        key=lambda item: item[0].travel_date,
    ):
        # Fail loudly here rather than at request time on a typo'd stop name.
        resolve_pickup(leg.source)
        resolve_dropoff(leg.destination)
        print(f"  {leg}   [{', '.join(sorted(names))}]")
