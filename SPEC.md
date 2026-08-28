# Project: Maine Oceanfront Listing Agent

Build an autonomous, zero-cost pipeline that finds new oceanfront listings on the Maine midcoast suitable for keeping a cruising sailboat on a mooring or dock during the sailing season, and emails me a report every morning.

**Ground rule: if any part of this spec is not achievable as written (blocked endpoint, unavailable data, cost that can't be avoided), stop and tell me plainly. Propose the closest achievable alternative. Do not silently degrade functionality or stub things out and call them done.**

## Hard constraints

- $0 to run. No paid APIs, no paid hosting, no LLM API calls. Free tiers and public data only.
- Fully autonomous once set up: runs daily without me touching it.
- Python. Keep dependencies minimal and pinned.

## Data sources

1. **Redfin (primary):** use the unofficial search/CSV endpoints with filters: State = Maine, waterfront properties, single-family + land, price cap per below. Restrict to these counties: **Sagadahoc, Lincoln, Knox, Waldo, Hancock**.
2. **Zillow (best-effort only):** attempt only if you can do it reliably for free without an arms race against bot detection. If not viable, say so and skip it — do not build something that will break in a week.
3. Deduplicate across sources by address.

## Filtering criteria

1. **Price:** main report section = listings under $2,000,000. Second section ("Above budget — meets all other criteria") = $2M–$4M, clearly separated. Never mix the two.
2. **Oceanfront:** must be saltwater frontage (ocean, bay, tidal river with ocean access). Exclude lakes and ponds.
3. **Sailboat viability test (critical).** Assume an average cruising sailboat: ~32–36 ft LOA, ~5.5 ft draft, seasonal use (May–October; no ice/winter screening needed). Three signals:
   - **Depth:** geocode the listing (free geocoder, e.g. US Census or Nominatim, respecting rate limits), then query free NOAA bathymetry/topobathy data (NOAA CUDEM topobathy DEMs and/or ENC chart soundings). Threshold: **≥9 ft at MLLW** (draft + under-keel clearance + margin below datum) reachable within ~150 m of the shoreline nearest the property. Report the actual measured depth and distance to the 9 ft contour, not just pass/fail.
   - **Existing boating activity (strong positive proxy):** check for charted mooring fields, anchorage areas, and marinas within ~1 km, using NOAA ENC features and/or OpenStreetMap. Proximity to an existing mooring field near-confirms viability even if bathymetry sampling is noisy; deep water with no nearby moorings and open exposure gets a "deep but possibly exposed" flag for my judgment.
   - **Access to open water:** if the property is up a tidal river or behind any charted fixed bridge or overhead cable, flag "upriver — verify overhead clearances" with the charted clearance if available (mast height ~50 ft). A full routing check is not required for v1; the flag is.
   - Classify overall: PASS / FAIL / UNKNOWN. UNKNOWN listings (missing data, low-confidence geocode) are included in the report with an explicit "unverified" flag — never silently dropped and never shown as PASS.
   - Document data sources, vertical datum handling, and known error sources in the README. This is a heuristic filter; label it as such in the report footer.

## Change detection

- Persist a state file (JSON or SQLite) of every listing seen, with price history.
- Each run, classify: NEW, PRICE CHANGE (report old → new), REMOVED/PENDING.
- Report only changes since the last run, not the full universe.

## Report + delivery

- HTML email, sent daily at 7:00 AM ET via Gmail SMTP with an app password (credentials from environment variables / repo secrets, never committed).
- Sections in order: (1) New under $2M, (2) Price cuts under $2M, (3) Above-budget passes ($2M–$4M), (4) Removed/pending, (5) Unverified.
- Each listing: address, town, price, listing URL, days on market, frontage description from the listing text, viability result (PASS/FAIL/UNKNOWN, measured depth + distance to 9 ft contour, mooring-field proximity, any bridge flag), and a link to the NOAA chart view at those coordinates so I can eyeball it.
- If there are no changes, send nothing (no empty emails).

## Scheduling

- Preferred: GitHub Actions cron on a schedule, committing state back to the repo.
- **Test this assumption early:** real estate sites may block GitHub's datacenter IPs. Verify Redfin responds from an Actions runner before building around it. If blocked, fall back to a local scheduler (cron/launchd on my Mac) and say that's what happened.

## Deliverables

- Working repo: fetcher(s), viability-checker module, differ, report renderer, sender, workflow file, README with setup steps (Gmail app password, secrets, how to adjust filters).
- A dry-run mode that prints the report to stdout instead of emailing.
- Run the full pipeline end-to-end at least once against live data before declaring it done, and show me the output.

## Known limitations to state in the README (do not paper over)

- Viability check is approximate: geocoding lands near the house, not the dock; data resolution is coarse; mooring rights, dock permits, harbor master waitlists, and wave exposure cannot be fully verified programmatically.
- Scraping unofficial endpoints can break without notice; the pipeline should fail loudly (error email or Actions failure notification), never silently.
