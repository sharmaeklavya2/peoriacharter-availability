# `peoriacharter-availability`

Checks [Peoria Charter](https://peoriacharter.com/) once a day for seats on the trips I care about
(mostly UIUC ↔ Purdue) and sends a desktop notification when something is available or when the check fails.

Stdlib only. No virtualenv, no dependencies.

Written by Claude, with a few edits from me.

## How it works

* `plans.py`: specifies which dates, source, destination to check.
* `availability.py`: checks <https://peoriacharter.com> for ticket availability.
* `check.py`: checks availability for all plans and notifies.

A **plan** is a function of today's date that expands into **legs**
(one directed lookup each: date, source, destination).
`check.py` collects the legs from every enabled plan, deduplicates them,
queries each one, prints the results, and notifies.

Trips already booked are listed in `booked.csv` and dropped before any request goes out.

## Setup

```sh
ln -s "$PWD/com.eklavya.peoriacharter-availability.plist" ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.eklavya.peoriacharter-availability.plist
launchctl kickstart -k gui/$(id -u)/com.eklavya.peoriacharter-availability   # force a run
```

Then check `check.log` and confirm a notification appeared.
The plist header has the rest of the `launchctl` incantations.

Notifications are attributed to **Script Editor** (that is who runs `osascript`);
if none appear, allow it in System Settings → Notifications.

`booked.csv` is gitignored, so recreate it after a fresh clone — see below.

## Daily use

**Whenever I book a ticket**, add a row to `booked.csv` or the checker keeps pinging me about seats I already have:

```csv
date,plan
2026-09-11,purdue-weekends
```

One row per (date, plan). Repeat the date to cover several plans.
`#` comments and blank lines are fine. A bad date, an empty plan, or a plan name not in `PLANS`
is a hard error rather than a silently skipped line.

Can be run by hand any time:

```sh
python3 check.py
python3 check.py --notify off
python3 plans.py    # show what would be checked, no network
```

Exit codes: `0` fine, `1` some leg failed, `2` bad invocation.

## Changing what is watched

To change what is watched, edit `PLANS` at the bottom of `plans.py`. Two factories:

- `weekend_trips(source, destination, weekends=4)` — Fri out, Sun back, as two
  one-way legs, anchored on the next Friday.
- `one_off(source, destination, on=..., starting_days_before=30)` — a single
  dated trip, checked only inside that window.

Plans expire themselves: they never emit a date before today, so `one_off` goes
quiet once the date passes and can be left in the list.
The disabled `winter-flight-ohare` plan is the template —
set the real date and `enabled=True`.

Stop names come from `PICKUP_STOPS` / `DROPOFF_STOPS` in `availability.py`.

## Things worth remembering

**The endpoint needs no auth.**
The booking page calls `/ajax/getRoutesForTicketSales?depart_at=MM/DD/YYYY&pickup=<id>&dropoff=<id>`
and attaches a Laravel `_token`, but the route ignores it — no token, cookies, or session required.
Adding `return_at` makes it a roundtrip query; there is no
`roundtrip` flag on this route (that belongs to the `/schedule` page).

**Pickup and dropoff IDs differ for the same place.**
Lilly Hall is 40 to depart from and 41 to arrive at;
ISR is 5 and 6. Not always adjacent (Golf Mill is 49/48, O'Hare 37/8), hence two maps.
The airports are also named differently on each side.

**Empty is not the same as broken.**
`fetch_buses` returns `[]` when the site answers and has nothing,
and raises `AvailabilityError` when the question could not be answered at all —
including an HTML page served with a 200, which is what a Cloudflare challenge looks like.
Keep that distinction if extending this.

**The launchd job is pinned to `/usr/bin/python3`**, which is 3.9.
It cannot be upgraded out from under the job, unlike brew's.
So no `match` statements and no `X | Y` evaluated at runtime.
Annotations are fine via `from __future__ import annotations`.

**launchd, not cron, on purpose.**
Asleep at 18:00 means the run happens on wake; days powered off are coalesced into one catch-up run, not a backlog.
No `RunAtLoad`, so logging in does not fire an extra run.

**Nothing rotates `check.log`.** Truncate it whenever.

## Etiquette

`robots.txt` is fully permissive and the terms say nothing about automated access.
The checker identifies itself in its `User-Agent`, spaces requests a second apart, and runs once a day.
Their JSON includes `"helloFriend": "Say hello, dev@peoriacharter.com"` — worth a note if this ever grows.
