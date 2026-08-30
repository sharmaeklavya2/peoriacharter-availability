"""
Query Peoria Charter's schedule endpoint for available buses.

Relies on an undocumented JSON endpoint from the site's booking page:
GET /ajax/getRoutesForTicketSales?depart_at=MM/DD/YYYY&pickup=<id>&dropoff=<id>

Stdlib only, so this runs under launchd/cron with the system interpreter and no virtualenv.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, date as Date, time as Time
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Mapping, Sequence


API_URL: Final[str] = "https://peoriacharter.com/ajax/getRoutesForTicketSales"
DATE_FORMAT: Final[str] = "%m/%d/%Y"
TIME_FORMAT: Final[str] = "%I:%M:%S %p"
DEFAULT_TIMEOUT: Final[float] = 20.0

# Identify ourselves rather than pretending to be a browser.
# Their robots.txt is fully permissive and their terms say nothing about automated access.
USER_AGENT: Final[str] = (
    "peoriacharter-availability/0.1 (once-daily seat check; ekurgn@gmail.com)"
)

# Stop IDs come from the `value` attributes of the two <select> elements on the homepage.
# Pickup and dropoff IDs are DISTINCT for the same place, probably because
# they identify the boarding and alighting points, which differ.
# Also, a name valid as a pickup is not necessarily valid as a dropoff.
PICKUP_STOPS: Final[Mapping[str, int]] = {
    "Armory (UIUC)": 3,
    "ISR (UIUC)": 5,
    "Illinois Terminal": 1,
    "Midway Airport": 11,
    "O'Hare Multi-Modal Facility": 37,
    "Downtown Chicago": 39,
    "Golf Mill": 49,
    "Old Orchard": 25,
    "Northbrook Court": 23,
    "Matteson": 21,
    "Downers Grove": 19,
    "Oakbrook Center": 17,
    "Woodfield Mall": 15,
    "PC Plaza": 27,
    "City Link": 29,
    "Bradley University": 31,
    "Watterson Commons": 33,
    "Uptown Station": 35,
    "Lilly Hall": 40,
}

DROPOFF_STOPS: Final[Mapping[str, int]] = {
    "Illinois Terminal": 2,
    "Armory (UIUC)": 4,
    "ISR (UIUC)": 6,
    "O'Hare Terminal 2 Departures": 8,
    "Midway Airport Departures": 12,
    "Downtown Chicago": 38,
    "Golf Mill": 48,
    "Old Orchard": 26,
    "Northbrook Court": 24,
    "Matteson": 22,
    "Downers Grove": 20,
    "Oakbrook Center": 18,
    "Woodfield Mall": 16,
    "PC Plaza": 28,
    "City Link": 30,
    "Bradley University": 32,
    "Watterson Commons": 34,
    "Uptown Station": 36,
    "Lilly Hall": 41,
}


class AvailabilityError(Exception):
    """Base class: the question 'are there seats?' could not be answered."""

class UnknownStop(AvailabilityError, KeyError):
    """A stop name was given that is not in STOPS."""

class RequestFailed(AvailabilityError):
    """The endpoint could not be reached, or returned a non-200 status."""

class MalformedResponse(AvailabilityError):
    """A response arrived but was not the JSON payload we expect.

    Raised for HTML challenge/error pages served with a 200, and for JSON whose
    shape has drifted. This must stay distinct from an empty result: 'no seats'
    and 'the check broke' are different outcomes.
    """


@dataclass(frozen=True, order=True)
class Bus:
    """A single departure returned by the endpoint."""

    departs_at: Time
    arrives_at: Time
    run: str
    pickup: str
    dropoff: str
    available_seats: int
    price: Decimal
    cash_price: Decimal
    route_id: int
    schedule_id: int

    @property
    def is_available(self) -> bool:
        return self.available_seats > 0

    def __str__(self) -> str:
        return (
            f"{self.run}: {self.departs_at:%I:%M %p} {self.pickup} -> "
            f"{self.arrives_at:%I:%M %p} {self.dropoff}, "
            f"${self.price} ({self.available_seats} seats)"
        )


def _resolve_stop(stop: int | str, stops: Mapping[str, int], role: str) -> int:
    """Return the numeric ID for a stop given by ID or by exact name."""
    if isinstance(stop, int):
        return stop
    try:
        return stops[stop]
    except KeyError:
        raise UnknownStop(f"unknown {role} {stop!r}") from None


def resolve_pickup(stop: int | str) -> int:
    """Return the numeric pickup ID for a stop given by ID or by exact name."""
    return _resolve_stop(stop, PICKUP_STOPS, "pickup")


def resolve_dropoff(stop: int | str) -> int:
    """Return the numeric dropoff ID for a stop given by ID or by exact name."""
    return _resolve_stop(stop, DROPOFF_STOPS, "dropoff")


def build_url(
    travel_date: Date,
    pickup: int | str,
    dropoff: int | str,
    return_date: Date | None = None,
) -> str:
    """Build the endpoint URL for one query."""
    params: dict[str, str] = {
        "depart_at": travel_date.strftime(DATE_FORMAT),
        "pickup": str(resolve_pickup(pickup)),
        "dropoff": str(resolve_dropoff(dropoff)),
    }
    # Presence of `return_at` is what makes the endpoint treat the query as a
    # roundtrip; there is no separate `roundtrip` flag on this route.
    if return_date is not None:
        params["return_at"] = return_date.strftime(DATE_FORMAT)
    return f"{API_URL}?{urllib.parse.urlencode(params)}"


def _fetch_payload(url: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch and validate the JSON body, or raise an AvailabilityError."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            content_type: str = response.headers.get_content_type()
            body: bytes = response.read()
    except urllib.error.HTTPError as exc:
        raise RequestFailed(f"HTTP {exc.code} from {url}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RequestFailed(f"could not reach {url}: {exc}") from exc

    # A Cloudflare interstitial is HTML served with a 200, so the status alone
    # is not evidence of success.
    if content_type != "application/json":
        raise MalformedResponse(
            f"expected application/json, got {content_type!r} ({len(body)} bytes)"
        )
    try:
        payload: Any = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MalformedResponse(f"response was not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "dRoutes" not in payload:
        raise MalformedResponse("response JSON has no 'dRoutes' key")
    if not isinstance(payload["dRoutes"], list):
        raise MalformedResponse("'dRoutes' was not a list")
    return payload


def _parse_bus(raw: Mapping[str, Any]) -> Bus:
    """Turn one `dRoutes` entry into a Bus, or raise MalformedResponse."""
    try:
        return Bus(
            departs_at=datetime.strptime(raw["pickup_at"], TIME_FORMAT).time(),
            arrives_at=datetime.strptime(raw["dropoff_at"], TIME_FORMAT).time(),
            run=str(raw["scheduleName"]),
            pickup=str(raw["pickup"]),
            dropoff=str(raw["dropoff"]),
            available_seats=int(raw["availableSeats"]),
            price=Decimal(str(raw["price"])),
            cash_price=Decimal(str(raw["cashPrice"])),
            route_id=int(raw["id"]),
            schedule_id=int(raw["schedule_id"]),
        )
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise MalformedResponse(f"could not parse route entry {raw!r}: {exc}") from exc


def fetch_buses(
    travel_date: Date,
    source: int | str,
    destination: int | str,
    *,
    min_seats: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
) -> Sequence[Bus]:
    """Return the buses running `source` -> `destination` on `travel_date`.

    Only buses with at least `min_seats` free seats are returned;
    pass `min_seats=0` to include sold-out departures.
    The result is sorted by departure time.

    An empty result means the endpoint answered and there is nothing available.
    Any failure to obtain a trustworthy answer raises AvailabilityError.
    """
    payload = _fetch_payload(
        build_url(travel_date, source, destination), timeout=timeout
    )
    buses = [_parse_bus(entry) for entry in payload["dRoutes"]]
    return sorted(bus for bus in buses if bus.available_seats >= min_seats)
