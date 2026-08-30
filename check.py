#!/usr/bin/env python
"""
Run every enabled plan and print the buses found for each leg.

One leg failing does not abandon the run: the failure is reported in place and
the remaining legs are still checked. The exit status is non-zero if any leg failed,
so a caller can distinguish 'nothing available' from a partly broken run.

Desktop notifications are sent when anything is available and when anything
failed. The notifier is chosen by platform (osascript on macOS, notify-send
elsewhere) and can be forced on or off with --notify.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date as Date
from typing import Callable, Collection, Final, Iterable, Optional, Sequence

from availability import AvailabilityError, Bus, fetch_buses
from plans import (
    BOOKED_PATH,
    PLANS,
    BookedEntry,
    Leg,
    Plan,
    collect_legs,
    load_booked,
)

REQUEST_SPACING: Final[float] = 1.0

# How many legs to name in a notification body before summarising the rest.
NOTIFICATION_DETAIL_LINES: Final[int] = 3

Notifier = Callable[[str, str], None]

# Passing title and body as argv avoids quoting them into AppleScript source,
# where an apostrophe ("O'Hare") or a quote would otherwise need escaping.
_OSASCRIPT_SOURCE: Final[str] = (
    "on run argv\n"
    "    display notification (item 2 of argv) with title (item 1 of argv)\n"
    "end run"
)


def _osascript_notify(title: str, body: str) -> None:
    subprocess.run(
        ["osascript", "-e", _OSASCRIPT_SOURCE, title, body],
        check=True,
        capture_output=True,
        timeout=10,
    )


def _notify_send(title: str, body: str) -> None:
    subprocess.run(
        ["notify-send", "--", title, body],
        check=True,
        capture_output=True,
        timeout=10,
    )


def detect_notifier() -> Optional[Notifier]:
    """Return a notifier for this system, or None if there is no obvious one."""
    if sys.platform == "darwin" and shutil.which("osascript"):
        return _osascript_notify
    if shutil.which("notify-send"):
        return _notify_send
    return None


def send(notifier: Optional[Notifier], title: str, body: str) -> None:
    """Send a notification, downgrading any failure to a warning.

    A broken notifier must not lose the run: the results are already on stdout.
    """
    if notifier is None:
        return
    try:
        notifier(title, body)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"warning: could not send notification: {exc}", file=sys.stderr)


@dataclass
class RunReport:
    """What a run found, for summarising after the fact."""

    checked: int = 0
    available: dict[Leg, Sequence[Bus]] = field(default_factory=dict)
    failed: dict[Leg, str] = field(default_factory=dict)


def check_plans(
    plans: Iterable[Plan],
    today: Date,
    booked: Collection[BookedEntry] = (),
) -> RunReport:
    """Print results for every leg of every enabled plan and report what happened."""
    report = RunReport()
    wanted: dict[Leg, set[str]] = collect_legs(plans, today, booked)
    if not wanted:
        print("No legs to check today.")
        return report

    legs: list[Leg] = sorted(wanted, key=lambda leg: (leg.travel_date, leg.source))
    report.checked = len(legs)
    print(f"Checking {len(legs)} legs for {today:%a %Y-%m-%d}\n")

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
            report.failed[leg] = f"{type(exc).__name__}: {exc}"
            print(f"    ! {report.failed[leg]}")
        else:
            if buses:
                report.available[leg] = buses
                for bus in buses:
                    print(f"    {bus}")
            else:
                print("    (nothing available)")
        print()
    return report


def _summarise(legs: Iterable[Leg]) -> str:
    """One line per leg, truncated so a notification stays readable."""
    listed: list[Leg] = sorted(legs, key=lambda leg: (leg.travel_date, leg.source))
    lines: list[str] = [
        f"{leg.travel_date:%a %b %d} {leg.source} -> {leg.destination}"
        for leg in listed[:NOTIFICATION_DETAIL_LINES]
    ]
    if len(listed) > NOTIFICATION_DETAIL_LINES:
        lines.append(f"...and {len(listed) - NOTIFICATION_DETAIL_LINES} more")
    return "\n".join(lines)


def notify_report(report: RunReport, notifier: Optional[Notifier]) -> None:
    """Send at most two notifications: one for seats, one for failures.

    One per leg would be eight banners on a normal day, which is how you teach
    yourself to dismiss them unread.
    """
    if report.available:
        seats: int = sum(
            bus.available_seats
            for buses in report.available.values()
            for bus in buses
        )
        send(
            notifier,
            f"Bus seats available ({seats} on {len(report.available)} legs)",
            _summarise(report.available),
        )
    if report.failed:
        send(
            notifier,
            f"Bus check failed ({len(report.failed)}/{report.checked} legs)",
            _summarise(report.failed),
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--notify",
        choices=("auto", "on", "off"),
        default="auto",
        help=(
            "auto: notify if this system has a notifier (default); "
            "on: require one, failing if absent; off: print only"
        ),
    )
    return parser.parse_args(argv)


def resolve_notifier(mode: str) -> Optional[Notifier]:
    """Pick a notifier for --notify `mode`, or raise if 'on' cannot be honoured."""
    if mode == "off":
        return None
    notifier: Optional[Notifier] = detect_notifier()
    if mode == "on" and notifier is None:
        raise RuntimeError(
            "--notify on, but found neither osascript nor notify-send"
        )
    return notifier


def main(argv: Optional[Sequence[str]] = None) -> int:
    args: argparse.Namespace = parse_args(argv)
    # Resolve the notifier before any network work, so an unusable --notify on
    # fails immediately rather than after the run it was meant to announce.
    try:
        notifier: Optional[Notifier] = resolve_notifier(args.notify)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # The file read happens here, at the entry point, so check_plans and
    # collect_legs stay pure functions of their arguments.
    booked: set[BookedEntry] = load_booked(BOOKED_PATH, PLANS)
    report: RunReport = check_plans(PLANS, Date.today(), booked)
    notify_report(report, notifier)

    if report.failed:
        print(
            f"{len(report.failed)} leg(s) could not be checked.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
