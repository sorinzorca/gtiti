"""
Submarine Cable Connector
=========================
TeleGeography's Submarine Cable Map (https://www.submarinecablemap.com) publishes
a free, unofficial JSON API behind its map UI. It has two endpoints we use:

  - /api/v3/cable/all.json    -> flat index of every cable: [{"id": ..., "name": ...}, ...]
                                  This is JUST an index. No owner or landing data here.
  - /api/v3/cable/{id}.json   -> full detail for ONE cable:
                                  {"owners": "Litgrid, Svenska Kraftnät",   # single comma-joined string, not a list
                                   "landing_points": [{"country": "Sweden", ...}, ...],
                                   "rfs": "2016", "rfs_year": 2016, "length": "400 km",
                                   "is_planned": false, ...}

NOTE ON THIS SCHEMA (as of 2026): TeleGeography previously exposed owners and
landing countries directly on the bulk /cable/all.json response (as GeoJSON
"properties"). They no longer do — the bulk endpoint is now just an id/name
index, and owners/landing data only exists per-cable. There is also no
/cable/{id}/details.json endpoint anymore (that 404s to an HTML app shell,
not JSON) — the correct per-cable URL is /cable/{id}.json.

Because there's no bulk owners field, "which cables does operator X belong to"
requires fetching every cable's detail JSON once and caching the result
in-memory (same TTL pattern as bgp_tools.py's ASN classification cache).

IMPORTANT CAVEAT: TeleGeography has no ASN or domain field anywhere in this
data, so operator matching is necessarily name-based (with a small alias table
for known name mismatches) — there's no way to match cables by ASN/domain
against this data source. Also, most retail/regional operators genuinely don't
own submarine cables (they lease IRU capacity from wholesale carriers), so a
"no memberships" result for e.g. a domestic mobile operator is often correct,
not a data gap.
"""

import re
import time
import asyncio
import aiohttp

TELEGEOGRAPHY_BASE = "https://www.submarinecablemap.com/api/v3"
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Full per-cable detail cache: {cable_id: {...}}. Rebuilt from scratch every
# _CACHE_TTL seconds. There are ~600 cables; TeleGeography exposes no
# last-modified/bulk-owners endpoint, so a timed full refresh is the simplest
# correct approach.
_detail_cache = None
_detail_cache_time = 0
_CACHE_TTL = 24 * 3600

# How many /cable/{id}.json requests to have in flight at once when building
# the cache. Keeps us polite to TeleGeography's (unofficial, undocumented) API.
_FETCH_CONCURRENCY = 20

# Manual aliases for operators whose common name doesn't resemble their
# TeleGeography "owners" string. Keys are normalized operator names (see
# _norm). Add entries here as you discover mismatches.
_ALIASES = {
    "tele2 sweden": ["tele2"],
    "digi romania": ["digi communications", "rcs rds", "rcsrds", "digi"],
}


def _norm(name):
    stripped = re.sub(r"[^a-z0-9 ]", " ", str(name).lower())
    return re.sub(r"\s+", " ", stripped).strip()


def _owner_names(owners_field):
    """TeleGeography returns owners as one comma-joined string (or null).
    Split it into individual owner names."""
    if not owners_field:
        return []
    return [o.strip() for o in str(owners_field).split(",") if o.strip()]


def _operator_matches(operator_name, owners_field):
    needle = _norm(operator_name)
    if not needle:
        return False
    needle_words = set(needle.split())
    candidates = [needle] + [_norm(a) for a in _ALIASES.get(needle, [])]
    for owner in _owner_names(owners_field):
        owner_norm = _norm(owner)
        if not owner_norm:
            continue
        for candidate in candidates:
            if candidate and (candidate in owner_norm or owner_norm in candidate):
                return True
        if len(needle_words) <= 2 and needle_words.issubset(set(owner_norm.split())):
            return True
    return False


async def _fetch_json(session, url):
    """GET a URL and return parsed JSON, or None if it's missing/not JSON.
    TeleGeography returns 200 + an HTML app shell for unknown routes instead
    of a clean 404, so we check Content-Type rather than trusting status."""
    async with session.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=aiohttp.ClientTimeout(total=15)) as r:
        if r.status != 200:
            return None
        if "json" not in r.headers.get("Content-Type", ""):
            return None
        return await r.json(content_type=None)


async def _get_cable_detail(session, cable_id, semaphore):
    async with semaphore:
        try:
            return await _fetch_json(session, f"{TELEGEOGRAPHY_BASE}/cable/{cable_id}.json")
        except Exception:
            return None


async def _build_detail_cache():
    global _detail_cache, _detail_cache_time
    async with aiohttp.ClientSession() as session:
        index = await _fetch_json(session, f"{TELEGEOGRAPHY_BASE}/cable/all.json")
        if not isinstance(index, list):
            raise RuntimeError("Unexpected response shape from /cable/all.json (expected a list)")
        semaphore = asyncio.Semaphore(_FETCH_CONCURRENCY)
        details = await asyncio.gather(
            *[_get_cable_detail(session, c.get("id", ""), semaphore) for c in index],
            return_exceptions=True,
        )
    cache = {}
    for summary, detail in zip(index, details):
        cable_id = summary.get("id", "")
        if not cable_id:
            continue
        if isinstance(detail, Exception) or not detail:
            detail = {}
        cache[cable_id] = {
            "cable_id": cable_id,
            "cable_name": summary.get("name", "") or detail.get("name", ""),
            "owners": detail.get("owners") or "",
            "landing_points": detail.get("landing_points") or [],
            "rfs": detail.get("rfs", ""),
            "rfs_year": detail.get("rfs_year", ""),
            "length": detail.get("length", ""),
            "is_planned": bool(detail.get("is_planned", False)),
        }
    _detail_cache = cache
    _detail_cache_time = time.time()
    return cache


async def _get_detail_cache():
    now = time.time()
    if _detail_cache is not None and (now - _detail_cache_time) < _CACHE_TTL:
        return _detail_cache
    try:
        return await _build_detail_cache()
    except Exception:
        # A failed refresh shouldn't nuke a previously-working cache.
        if _detail_cache is not None:
            return _detail_cache
        raise


async def get_operator_cables(operator_name):
    try:
        cache = await _get_detail_cache()
    except Exception as exc:
        return {"operator": operator_name, "cable_memberships": [], "total_cables": 0, "error": f"Failed to fetch cable data: {exc}"}
    memberships = []
    for entry in cache.values():
        if _operator_matches(operator_name, entry["owners"]):
            countries = sorted({lp.get("country", "") for lp in entry["landing_points"] if lp.get("country")})
            memberships.append({
                "cable_name": entry["cable_name"],
                "cable_id": entry["cable_id"],
                "rfs_date": entry["rfs"] or entry["rfs_year"],
                "cable_length": entry["length"],
                "landing_countries": countries,
                "is_planned": entry["is_planned"],
                "all_owners": _owner_names(entry["owners"]),
                "telegeography_url": f"https://www.submarinecablemap.com/#/submarine-cable/{entry['cable_id']}",
            })
    if not memberships:
        return {
            "operator": operator_name,
            "cable_memberships": [],
            "total_cables": 0,
            "note": (
                f"No cable memberships found for '{operator_name}'. This is often correct: most "
                "retail/regional operators lease international capacity rather than owning cables "
                "outright. If you expected a match, try the parent company or a shorter name."
            ),
            "data_source": "TeleGeography Submarine Cable Map",
        }
    memberships.sort(key=lambda m: m["cable_name"])
    return {"operator": operator_name, "cable_memberships": memberships, "total_cables": len(memberships), "data_source": "TeleGeography Submarine Cable Map"}


async def get_cables_by_country(country_name):
    try:
        cache = await _get_detail_cache()
    except Exception as exc:
        return {"country": country_name, "cables": [], "total_cables": 0, "error": str(exc)}
    country_norm = _norm(country_name)
    matching = []
    for entry in cache.values():
        countries = [lp.get("country", "") for lp in entry["landing_points"]]
        if any(country_norm in _norm(c) for c in countries if c):
            matching.append({
                "cable_name": entry["cable_name"],
                "cable_id": entry["cable_id"],
                "rfs_date": entry["rfs"] or entry["rfs_year"],
                "owners": _owner_names(entry["owners"]),
                "is_planned": entry["is_planned"],
                "telegeography_url": f"https://www.submarinecablemap.com/#/submarine-cable/{entry['cable_id']}",
            })
    matching.sort(key=lambda c: c["cable_name"])
    return {"country": country_name, "cables": matching, "total_cables": len(matching), "data_source": "TeleGeography Submarine Cable Map"}
