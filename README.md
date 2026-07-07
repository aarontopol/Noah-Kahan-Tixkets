# Noah-Kahan-Tixkets

An agent that watches resale/primary ticket sources for **Noah Kahan — _The Great
Divide Tour_ at Coors Field, Denver (Aug 8 & 9, 2026)** and **texts your phone**
when 4 seats together come up in the lower bowl under your price.

It looks for listings that meet **all** of your rules:

- 🎟️ **4 seats next to each other** (contiguous)
- 📍 **Lower bowl, sections 120–141**
- 💵 **At or below your price** (starts at **$350/ticket**, configurable)
- 🚫 **No obstructed / limited-view seats**
- 🗓️ **Either show** — Aug 8 or Aug 9, 2026

When something qualifies, you get a text like:

```
🎫 Noah Kahan Aug 8 @ Coors Field: 2 seat(s) under target!
• Sec 120 Row 3 x6 @ $289/ea (seatgeek)
• Sec 128 Row 12 x4 @ $312/ea (ticketmaster)
https://…link-to-cheapest-listing…
```

It remembers what it already told you, so you only get pinged for **new**
listings or when a price **drops** further.

---

## Quick start (run once locally)

```bash
pip install -r requirements.txt
cp .env.example .env          # then edit .env with your keys/phone
set -a && . ./.env && set +a  # load .env into the environment
python -m monitor --dry-run   # prints the text instead of sending it
```

Drop `--dry-run` to actually send SMS, or add `--loop` to keep polling on the
interval in `config.yaml`.

```bash
python -m monitor             # one real check
python -m monitor --loop      # poll every 15 min (see config.yaml)
```

## Recommended: run it in the cloud on a schedule (GitHub Actions)

No machine to keep on. The included workflow
(`.github/workflows/monitor.yml`) runs every ~15 minutes and holds your
credentials as encrypted secrets.

1. Push this repo to GitHub.
2. Go to **Settings → Secrets and variables → Actions → New repository secret**
   and add:

   | Secret | Required? | Where to get it |
   | --- | --- | --- |
   | `TEXTBELT_KEY` | ✅ yes | https://textbelt.com (buy credits; or `textbelt_test` to trial) |
   | `ALERT_PHONE` | ✅ yes | your mobile, e.g. `+14044443292` |
   | `TICKETMASTER_API_KEY` | recommended | free at https://developer.ticketmaster.com |
   | `SEATGEEK_CLIENT_ID` | optional | free at https://platform.seatgeek.com |
   | `STUBHUB_TOKEN` | optional | https://developer.stubhub.com (partner approval) |

   (Optional) add a **Variable** `MAX_PRICE_PER_TICKET` to override the price
   without editing `config.yaml`.
3. Open the **Actions** tab, pick **Noah Kahan ticket monitor**, and click
   **Run workflow** (tick “dry run” the first time to test wiring). After that
   it runs automatically on the schedule.

## Changing what it looks for

Everything except secrets lives in [`config.yaml`](./config.yaml):

```yaml
criteria:
  section_min: 120          # lower-bowl range
  section_max: 141
  min_quantity: 4           # seats together
  max_price_per_ticket: 350 # <-- your alert threshold
  require_contiguous: true
  exclude_obstructed: true
```

Change the number under `max_price_per_ticket` to raise/lower your threshold,
adjust the section range, or edit the `dates` list. Commit and push; the next
scheduled run uses the new values.

## How the sources work (and their limits)

The agent is **provider-agnostic** — it merges listings from every enabled
source, then applies your filters. Honest notes on each:

| Source | Key needed | Seat-level detail |
| --- | --- | --- |
| **Ticketmaster** | free API key | Discovery API confirms the events + price ranges; the public seat-map endpoint adds **per-section** cheapest price & availability. This is the main workhorse. |
| **SeatGeek** | free client id | Public API gives event-level lowest price; full per-seat listings need a partner key. Parses seat-level data when your key returns it. |
| **StubHub** | partner token | Richest seat-level data (section, row, seat numbers, obstructed flag) — but requires approved developer access. Off by default. |
| **mock** | none | Sample data in `data/sample_listings.json` for testing/demo. |

Two honest caveats baked into the design:

- **Contiguity:** when a source exposes seat numbers we verify a run of 4
  consecutive seats. When it only exposes section-level availability
  (Ticketmaster facets), “4 together” is approximated by “≥4 available in that
  section.” StubHub gives true seat numbers.
- **Obstructed view:** excluded via an explicit flag when the source provides
  one, plus a keyword scan of listing notes (“obstructed”, “limited view”,
  “behind pole”, …).

If a source is unconfigured or errors, it’s skipped — one bad source never
stops the others.

## Project layout

```
monitor/
  __main__.py        CLI (run once / --loop / --dry-run)
  agent.py           orchestrate: fetch → filter → dedupe → text
  config.py          load config.yaml + env secrets
  models.py          Listing data model
  filters.py         the matching rules (section/price/contiguous/obstructed)
  state.py           dedupe store (don't re-text the same seats)
  notifier.py        TextBelt SMS
  providers/         seatgeek, ticketmaster, stubhub, mock
data/sample_listings.json   demo data for the mock provider
tests/               pytest suite (filters, state, notifier, end-to-end)
.github/workflows/monitor.yml   scheduled cloud runner
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Notes

- TextBelt and the ticket APIs cost a little money / are rate-limited; the
  15-minute cadence and dedupe keep usage low.
- This tool only **notifies** — it does not buy tickets. Follow the link in the
  text to purchase.
