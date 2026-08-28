"""Render the daily diff into an HTML email report."""
import html
from datetime import datetime

from . import config

NOAA_CUSTOM_CHART_URL = "https://devgis.charttools.noaa.gov/pod/"

_STYLE = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #1a1a1a; max-width: 900px; margin: 0 auto; padding: 16px; }
h1 { font-size: 20px; }
h2 { font-size: 16px; border-bottom: 2px solid #0a4a6b; padding-bottom: 4px; margin-top: 32px; }
.listing { border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; margin: 10px 0; }
.listing h3 { margin: 0 0 6px 0; font-size: 15px; }
.meta { color: #555; font-size: 13px; margin-bottom: 6px; }
.price { font-weight: bold; font-size: 15px; }
.price-cut { color: #b00020; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: bold; color: white; }
.badge-pass { background: #1b8a3a; }
.badge-unknown { background: #a06a00; }
.note { font-size: 13px; margin: 4px 0; color: #333; }
.autogen { font-size: 12px; color: #666; font-style: italic; }
.links a { margin-right: 12px; font-size: 13px; }
footer { margin-top: 32px; padding-top: 12px; border-top: 1px solid #ccc; font-size: 12px; color: #777; }
.empty { color: #777; font-style: italic; }
"""


def _fmt_price(p):
    return f"${p:,.0f}" if p is not None else "N/A"


def _frontage_summary(row):
    parts = [row["oceanfront_note"] or ""]
    if row["mooring_nearby"]:
        parts.append(f"Charted mooring field/marina ~{row['mooring_distance_m']:.0f}m away.")
    else:
        parts.append("No charted mooring field/marina within 1km.")
    if row["bridge_flag"] and row["bridge_note"]:
        parts.append(row["bridge_note"])
    return " ".join(p for p in parts if p)


def _depth_summary(row):
    if row["measured_depth_ft"] is None:
        return "No usable NOAA bathymetry data found near this shoreline."
    if row["distance_to_9ft_m"] is not None:
        return (
            f"Reaches {config.MIN_DEPTH_FT_MLLW:.0f}ft MLLW at ~{row['distance_to_9ft_m']:.0f}m "
            f"from shore (max sampled within 150m: {row['measured_depth_ft']:.1f}ft)."
        )
    return (
        f"Did not reach {config.MIN_DEPTH_FT_MLLW:.0f}ft MLLW within 150m of shore "
        f"(max sampled: {row['measured_depth_ft']:.1f}ft)."
    )


def _chart_link(row):
    return f"{NOAA_CUSTOM_CHART_URL}?q={row['lat']},{row['lon']}"


def _badge(overall):
    if overall == "PASS":
        return '<span class="badge badge-pass">PASS</span>'
    if overall == "UNKNOWN":
        return '<span class="badge badge-unknown">UNVERIFIED</span>'
    return f'<span class="badge">{html.escape(overall)}</span>'


def _listing_html(row, price_note=None):
    addr = html.escape(row["address"] or "")
    city = html.escape(row["city"] or "")
    county = html.escape(row["county"] or "")
    url = html.escape(row["url"] or "")
    dom = row["days_on_market"]
    dom_str = f"{dom} days on market" if dom is not None else "days on market unknown"

    price_line = f'<span class="price">{_fmt_price(row["price"])}</span>'
    if price_note:
        price_line += f' <span class="price-cut">{html.escape(price_note)}</span>'

    return f"""
    <div class="listing">
      <h3>{addr}, {city} ({county} County) {_badge(row["viability_overall"])}</h3>
      <div class="meta">{price_line} &middot; {dom_str} &middot; {html.escape(row["property_type"] or "")}</div>
      <div class="note"><b>Depth:</b> {html.escape(_depth_summary(row))}</div>
      <div class="note"><b>Frontage/context:</b> {html.escape(_frontage_summary(row))}
        <div class="autogen">(auto-generated from geodata, not quoted listing text — see the Redfin link for the actual MLS description)</div>
      </div>
      <div class="links">
        <a href="{url}">View on Redfin &rarr;</a>
        <a href="{_chart_link(row)}">NOAA chart view (enter/search these coordinates: {row['lat']}, {row['lon']}) &rarr;</a>
      </div>
    </div>
    """


def _section(title, items_html):
    if not items_html:
        return f"<h2>{html.escape(title)}</h2><p class='empty'>None.</p>"
    return f"<h2>{html.escape(title)}</h2>{''.join(items_html)}"


def render_report(diff, run_dt=None) -> str:
    """Render the full HTML report. Caller should check diff has any
    content first (has_changes) -- this always renders something."""
    run_dt = run_dt or datetime.now()

    new_main_html = [_listing_html(row) for (row,) in diff.new_under_main]
    price_cuts_html = [
        _listing_html(row, price_note=f"(was {_fmt_price(old)})")
        for row, old in diff.price_cuts_under_main
    ]
    above_budget_html = []
    for row, old_price, is_new in diff.above_budget_passes:
        note = None if is_new else f"(was {_fmt_price(old_price)})"
        above_budget_html.append(_listing_html(row, price_note=note))
    removed_html = [_listing_html(row) for (row,) in diff.removed]
    unverified_html = []
    for row, old_price, is_new in diff.unverified:
        note = None if is_new else f"(was {_fmt_price(old_price)})"
        unverified_html.append(_listing_html(row, price_note=note))

    warnings_html = ""
    if diff.run_warnings:
        items = "".join(f"<li>{html.escape(w)}</li>" for w in diff.run_warnings)
        warnings_html = f"<div class='note'><b>Run warnings:</b><ul>{items}</ul></div>"

    body = f"""
    <style>{_STYLE}</style>
    <h1>Maine Oceanfront Listings — {run_dt.strftime('%A, %B %-d, %Y')}</h1>
    <p class="meta">Counties: {', '.join(config.TARGET_COUNTIES)} &middot; Tracked reportable listings: {diff.total_reportable_tracked}</p>
    {warnings_html}
    {_section("New — under $2M", new_main_html)}
    {_section("Price cuts — under $2M", price_cuts_html)}
    {_section("Above budget — meets all other criteria ($2M–$4M)", above_budget_html)}
    {_section("Removed / pending", removed_html)}
    {_section("Unverified (viability could not be confirmed)", unverified_html)}
    <footer>
      Sailboat viability (depth, mooring proximity, bridge clearance) is a <b>heuristic screen</b> based on
      free NOAA bathymetry/VDatum data and OpenStreetMap, not a substitute for a chart, a depth sounder, or
      local knowledge. Geocoding lands near the house, not necessarily the dock. See the repo README for
      full methodology and known limitations.
    </footer>
    """
    return body


def has_changes(diff) -> bool:
    return bool(
        diff.new_under_main or diff.price_cuts_under_main or diff.above_budget_passes
        or diff.removed or diff.unverified
    )
