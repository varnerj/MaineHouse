# Maine Oceanfront Listing Agent

Autonomous, zero-cost pipeline that finds oceanfront listings on the Maine
midcoast (Sagadahoc, Lincoln, Knox, Waldo, Hancock counties) suitable for
keeping a cruising sailboat on a mooring or dock during the sailing season,
and emails a daily report of what's new or changed.

Full requirements are in [`SPEC.md`](SPEC.md). This README covers what was
actually built, how it deviates from the spec (and why), setup, and known
limitations.

## Deviations from the spec (read this first)

The spec's ground rule was: if something isn't achievable as written, stop,
say so, and propose the closest achievable alternative rather than silently
degrading. Three things came up during build:

1. **Redfin region/county search and the `waterfront=true` filter aren't
   usable.** Redfin's `location-autocomplete` endpoint (used to resolve a
   county name to Redfin's internal `region_id`) and its sitemap files are
   blocked at the CloudFront edge for every source IP tested, including
   GitHub Actions runners. The `waterfront=true` query parameter is silently
   ignored by the `gis-csv` endpoint we do have access to. Workaround: query
   `gis-csv` with an explicit lat/lon bounding polygon per county instead of
   a region id, and do county attribution and oceanfront/waterfront
   classification ourselves (`towns.py`, `geo_context.py`, `viability.py`).
2. **Per-listing MLS remarks ("frontage description from the listing
   text") aren't reachable.** Redfin's listing detail pages and their JSON
   hydration endpoints return HTTP 403 consistently, even though the bulk
   search endpoint doesn't. Only a real browser gets through. Per your
   choice, each listing instead gets an **auto-generated context summary**
   built from our own geodata (nearest saltwater feature + distance,
   mooring/marina proximity, bridge flag), clearly labeled as computed, plus
   the direct Redfin URL so you can read the actual MLS remarks yourself.
3. **NOAA's "Custom Chart" viewer doesn't have a documented deep-link URL
   scheme** (it's an older Esri Web AppBuilder app with no simple `?lat=&
   lon=` contract, and the ENC MapServer's raster `/export` endpoint returns
   a blank image without pre-resolving specific ENC cell layers). The report
   links to the general NOAA Custom Chart tool with the coordinates printed
   next to it for you to enter, rather than a broken or fabricated deep
   link.

Zillow was evaluated and dropped per the spec's own instruction: it has no
comparably-accessible unofficial search endpoint, and scraping its rendered
pages would be exactly the "arms race against bot detection" the spec said
to avoid. Redfin is the sole listing source.

## How it works

1. **Fetch** (`redfin.py`): pulls active single-family + land listings from
   Redfin's `stingray/api/gis-csv` endpoint using a lat/lon bounding box per
   target county (see `config.COUNTY_SEARCH_BOXES`). The endpoint silently
   truncates at ~350 rows, so boxes that hit the cap are recursively split
   into quadrants and re-queried until every leaf box is under the cap —
   this avoids silently dropping listings in higher-inventory counties.
2. **County + price filter**: rows are matched to a target county by their
   Redfin "city" field against a static town/village lookup (`towns.py`),
   then to price ≤ $4,000,000.
3. **Oceanfront + viability** (`geo_context.py`, `bathymetry.py`,
   `viability.py`): one OpenStreetMap Overpass query per listing (radius
   1200m) finds the nearest coastline/tidal water, nearest freshwater body,
   nearest mooring field/marina, and nearest bridge/power line. If saltwater
   is within 300m, the listing is classified oceanfront and:
   - **Depth**: NOAA's NCEI DEM mosaic ImageServer (CUDEM topobathy) is
     queried point-by-point along a small fan of bearings from the nearest
     shoreline point out to 150m, converted from NAVD88 to MLLW using a
     per-point NOAA VDatum offset (falling back to a documented ~1.7m
     constant if VDatum is unreachable or returns its out-of-coverage
     sentinel). Multiple bearings are sampled because the direct
     property→shoreline bearing can run along or back onto land near coves
     and headlands; the most favorable result is kept.
   - **Mooring/marina proximity**: from the same Overpass call, within 1km.
   - **Bridge/overhead-cable flag**: a bridge or power line within 800m,
     roughly between the property and open water (±60° of the
     shoreline bearing), flags "upriver — verify overhead clearances".
   - These combine into **PASS / FAIL / UNKNOWN** per the decision table in
     `viability.py` (depth-confirmed + mooring nearby → PASS; depth alone →
     PASS with a "deep but possibly exposed" note; mooring alone (no depth
     confirmation) → PASS with a caveat; no signal at all → UNKNOWN; a
     confirmed shallow reading with no mooring → FAIL).
4. **State + diff** (`state.py`, `pipeline.py`): every PASS/UNKNOWN listing
   is upserted into `data/state.sqlite3` with full price history. Viability
   is computed once per listing and cached — it never changes, so re-runs
   only pay the Overpass/NOAA cost for genuinely new listings. Each run
   classifies NEW, PRICE_CHANGE, and REMOVED against the previous state.
5. **Report + send** (`report.py`, `mailer.py`): an HTML email is built
   from the diff only (not the full universe) and sent via Gmail SMTP with
   an app password. If there are no changes, nothing is sent.

## Setup

1. **Gmail app password**: enable 2-Step Verification on the sending Gmail
   account, then create an app password at
   <https://myaccount.google.com/apppasswords> (App: Mail). This is a
   16-character password, not your normal Gmail password.
2. **Repo secrets** (Settings → Secrets and variables → Actions):
   - `EMAIL_FROM` — the sending Gmail address.
   - `GMAIL_APP_PASSWORD` — the app password from step 1.
   - `EMAIL_TO` — where the report should go (can be the same as
     `EMAIL_FROM`).
   - The workflow maps these to the env var names `mailer.py` reads
     (`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_TO_ADDRESS`) --
     see `.github/workflows/daily-report.yml`. Running `main.py` locally
     uses the `mailer.py` names directly (see "Running locally" below).
   - Your GitHub personal access token used locally to push must include
     the `workflow` scope to push changes to files under
     `.github/workflows/`; this isn't needed for the daily run itself
     (which uses the built-in `GITHUB_TOKEN`), only for you to edit the
     workflow file from your machine later.
3. The workflow (`.github/workflows/daily-report.yml`) runs on a schedule
   and commits `data/state.sqlite3` back to the repo after each run — no
   external database needed.

### Running locally

```bash
export PYTHONPATH=src
python3 -m maine_agent.main --dry-run                  # prints the report, sends nothing
python3 -m maine_agent.main --dry-run --limit-new 10    # fast partial run for testing
python3 -m maine_agent.main --dry-run --limit-new 0     # no cap: process the entire backlog in one run
python3 -m maine_agent.main                             # sends via Gmail (needs env vars set)
```

### Initial backlog / first few days

Every newly-seen listing costs one OpenStreetMap Overpass call plus, for
oceanfront ones, up to ~55 NOAA DEM calls and a VDatum call -- against the
shared free public Overpass instance this was ~10-20s/listing in testing.
The first-ever run has no cache and faces the full county-wide candidate
set (~1,700 listings for the 5 target counties as of initial testing), which
at that rate would take multiple hours in one run and risk exceeding an
Actions job's timeout. To avoid that, each run assesses at most
`config.MAX_NEW_ASSESSMENTS_PER_RUN` (default 250) newly-seen listings;
already-assessed listings are cached and free on every later run regardless
of this cap. In practice this means **the first several scheduled runs will
each report a batch of newly-discovered listings** as the backlog is
cleared (roughly a week at the default cap and observed rate), after which
daily runs only see genuinely new/changed listings and finish quickly. To
process everything in one sitting instead (e.g. for a one-time local
backfill), run with `--limit-new 0`.

**Observed during development**: running repeated test batches against
`overpass-api.de` from one machine over about two hours (several hundred
requests total, well above what any single day's production run sends) got
that IP's connections actively refused for an extended period -- the public
instance's documented abuse-prevention behavior. A single scheduled run
(≤250 new-listing Overpass calls, from a fresh GitHub Actions runner IP
each time) is a much smaller burst and wasn't observed to trigger this, but
if it ever does, `geo_context.py` currently has no automatic mirror
fallback or backoff-and-retry-later behavior for a sustained block --
individual failed calls are caught and degrade that listing to UNKNOWN
(never a false PASS), but a blocked run will report a batch of listings as
Unverified rather than fully assessed. Worth adding mirror fallback
(`overpass.kumi.systems` and others exist) if this recurs in practice.

### Adjusting filters

- Price bands, depth threshold, mooring/bridge radii, draft/mast
  assumptions: `src/maine_agent/config.py`.
- Target counties and their search bounding boxes: `config.py`
  (`TARGET_COUNTIES`, `COUNTY_SEARCH_BOXES`).
- Town/village → county lookup (used to attribute Redfin's "city" field to
  a target county): `src/maine_agent/towns.py`.

## Scheduling

GitHub Actions cron is UTC-only and does not follow DST. The workflow is
scheduled at 11:00 UTC, which is 7:00 AM ET during Eastern Daylight Time
(roughly March–November) and 6:00 AM ET during Eastern Standard Time. This
was verified reachable from an Actions runner before being adopted (see
"GitHub Actions reachability" below) — no local-scheduler fallback was
needed.

## Known limitations (heuristic filter — do not treat as authoritative)

- **Geocoding accuracy**: listing coordinates come directly from Redfin's
  MLS data (more accurate than a text geocoder for this purpose), but they
  still locate the house/parcel centroid, not the dock or the actual
  waterline. For large waterfront lots this can be off by tens to a couple
  hundred meters.
- **Depth data resolution and currency**: NOAA's CUDEM topobathy mosaic is
  the best free national bathymetry dataset but tiles vary in survey date
  and source; a DEM grid cell is an average, not a sounding, and NoData
  gaps are common right at the shoreline (the exact zone MLLW conversion
  matters most).
- **Vertical datum handling**: DEM elevations are NAVD88; converting to
  MLLW uses NOAA's VDatum API per listing (real per-point offset, since it
  varies meaningfully — roughly 1.2–2.5m — across this stretch of coast).
  When VDatum is unreachable or returns its out-of-coverage sentinel value,
  a fallback constant (~1.7m, `config.FALLBACK_NAVD88_TO_MLLW_OFFSET_M`) is
  used instead and the report notes which source was used
  (`datum_source`).
- **Depth-sampling bearing is a heuristic, not a routing engine**: samples
  run along a small fan of compass bearings from the nearest charted
  shoreline point. Near coves, points, and complex shorelines this can
  still miss the actual navigable channel, producing a false FAIL (a real
  channel exists but wasn't sampled) more often than a false PASS. A full
  routing check was explicitly out of scope for v1 per the spec.
- **Mooring rights, dock permits, harbor master waitlists, wave/wind
  exposure**: none of this can be verified programmatically. "Deep but
  possibly exposed" and "PASS via mooring proximity, bathymetry
  inconclusive" notes exist specifically to flag where your judgment is
  still required.
- **Bridge/overhead clearance flag is coarse**: it flags any bridge or
  power line within 800m roughly on the bearing toward open water; it
  reports OSM's charted clearance when tagged (rare) but does not verify
  actual navigability.
- **Oceanfront/freshwater classification depends on OpenStreetMap tagging
  completeness.** A coastline or tidal-water feature must be tagged in OSM
  within 300m of the listing for it to be classified oceanfront; sparse
  OSM coverage in a specific cove could cause a real waterfront listing to
  be missed. This is functionally the same risk as an UNKNOWN Overpass
  failure and is handled the same way (see below) only when the query
  itself fails, not when it succeeds but returns no nearby water — the
  latter is treated as a confident "not oceanfront." This is the single
  biggest theoretical source of false exclusions in the pipeline.
- **Town/village → county lookup is manually maintained**
  (`towns.py`), not sourced from an authoritative dataset. It covers all
  legal municipalities in the 5 target counties plus common coastal postal
  villages, but an obscure or unlisted village name would cause a listing
  to be excluded rather than flagged.
- **County search boxes can still hit Redfin's row cap** in unusually
  high-inventory conditions; the adaptive quadrant-splitting in
  `redfin.py` handles this up to `MAX_SPLIT_DEPTH` (6 levels, 4096 leaf
  boxes) and logs an error (not a silent drop) if a box is still capped at
  that depth.
- **A tracked listing whose price rises above $4M reads as "Removed/pending",**
  not "priced out of range" — the fetch itself filters to ≤$4M before
  diffing, so a listing that jumps above the upper band looks identical to
  one that left Redfin's active set. If it drops back under $4M later it
  reappears and is reported as NEW again. Large upward price jumps are rare
  in practice; this was judged not worth a third status category for v1.
- **A crash partway through a run has a narrow silent-notification gap.**
  State commits periodically during a run (every 20 listings) so an
  interrupted run doesn't lose already-computed NOAA/Overpass results and
  force redoing that work. Per-listing network errors are already caught
  and turned into an UNKNOWN row, so this only matters for a genuinely
  unhandled crash (not a network hiccup). If that happens between a
  periodic commit and the run's email send, a listing committed just before
  the crash is no longer "new" on the next run and its one-time NEW/price-
  change notification would not be re-sent. Rare in practice; a "notified"
  column separate from "seen" would close this gap if it ever matters.
- **Scraping unofficial endpoints can break without notice.** The pipeline
  fails loudly: a non-zero exit fails the Actions job (which triggers
  GitHub's own workflow-failure notification), and `main.py` additionally
  attempts to send a failure-notice email if credentials are configured.

## GitHub Actions reachability

Verified directly, not assumed: a probe workflow was pushed and run on an
actual `ubuntu-latest` Actions runner (confirmed egress IP was an Azure
datacenter address, not a residential/office IP). The `gis-csv` endpoint
returned HTTP 200 with a full set of live listing rows, identical in shape
to a local run — Redfin does not block this specific endpoint for Actions
runner IPs. (Redfin's full HTML pages and JSON detail endpoints, used only
opportunistically for remarks text, are blocked everywhere including
locally — see "Deviations from the spec" above; this is unrelated to
runner IP and not something a different host would fix.)

## Repo layout

```
src/maine_agent/
  config.py       thresholds, price bands, county search boxes
  httpclient.py   stdlib-only HTTP helper (no third-party dependencies)
  towns.py        town/village -> county lookup
  geomath.py      haversine/bearing/point-to-segment helpers
  redfin.py       Redfin gis-csv fetch + adaptive box splitting
  geo_context.py  combined OSM Overpass query (coastline/water/moorings/bridges)
  bathymetry.py   NOAA DEM depth sampling + VDatum datum conversion
  viability.py    PASS/FAIL/UNKNOWN classifier
  state.py        SQLite persistence (data/state.sqlite3)
  pipeline.py     fetch -> assess -> upsert -> diff, one run
  report.py       HTML email renderer
  mailer.py       Gmail SMTP send / --dry-run stdout
  main.py         CLI entrypoint
data/state.sqlite3          committed by the daily workflow
.github/workflows/daily-report.yml
```
