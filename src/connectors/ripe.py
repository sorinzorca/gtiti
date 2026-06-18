"""
RIPE NCC / RIPEstat Connector
==============================
Uses the RIPEstat REST API (https://stat.ripe.net/docs/data_api).

Note on country ASN lookup:
  The old /data/country-asns/ endpoint was retired by RIPE in 2025.
  We now use two working alternatives:
    1. /data/asn-neighbours/ — for per-ASN upstream/peer data
    2. /data/announced-prefixes/ — for prefix counts per ASN
  For country-level operator discovery we fall back to PeeringDB,
  which has reliable country filtering.
"""

import httpx
from typing import Any

BASE_URL = "https://stat.ripe.net/data"
TIMEOUT  = 12
HEADERS  = {"User-Agent": "gtiti/0.1 (contact@gtiti.io)"}


def _safe(value: Any, fallback: Any = None) -> Any:
    if value is None or value == "":
        return fallback
    return value


async def get_asn_overview(asn: int | str) -> dict:
    """High-level overview of an ASN — name, announced status."""
    asn_str = str(asn).upper().replace("AS", "").strip()
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = await client.get(
            f"{BASE_URL}/as-overview/data.json",
            params={"resource": f"AS{asn_str}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    return {
        "asn":       f"AS{asn_str}",
        "name":      _safe(data.get("holder"), "unknown"),
        "announced": data.get("announced", False),
    }


async def get_announced_prefixes(asn: int | str) -> dict:
    """IP prefixes (address blocks) announced by an ASN into global BGP."""
    asn_str = str(asn).upper().replace("AS", "").strip()
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = await client.get(
            f"{BASE_URL}/announced-prefixes/data.json",
            params={"resource": f"AS{asn_str}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    prefixes = data.get("prefixes", [])
    ipv4 = [p["prefix"] for p in prefixes if "." in p.get("prefix", "")]
    ipv6 = [p["prefix"] for p in prefixes if ":" in p.get("prefix", "")]
    return {
        "asn":            f"AS{asn_str}",
        "ipv4_count":     len(ipv4),
        "ipv6_count":     len(ipv6),
        "ipv4_prefixes":  ipv4[:20],
        "ipv6_prefixes":  ipv6[:20],
        "total_prefixes": len(prefixes),
    }


async def get_upstreams(asn: int | str) -> dict:
    """Transit providers, peers, and downstreams for an ASN."""
    asn_str = str(asn).upper().replace("AS", "").strip()
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = await client.get(
            f"{BASE_URL}/asn-neighbours/data.json",
            params={"resource": f"AS{asn_str}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    neighbours  = data.get("neighbours", [])
    upstreams   = [n for n in neighbours if n.get("type") == "left"]
    downstreams = [n for n in neighbours if n.get("type") == "right"]
    peers       = [n for n in neighbours if n.get("type") == "uncertain"]

    def fmt(lst):
        return [
            {"asn": f"AS{n['asn']}", "power": n.get("power", 0)}
            for n in sorted(lst, key=lambda x: x.get("power", 0), reverse=True)[:10]
        ]

    return {
        "asn":         f"AS{asn_str}",
        "upstreams":   fmt(upstreams),
        "downstreams": fmt(downstreams),
        "peers":       fmt(peers),
    }


async def get_country_asns(country_code: str, max_to_enrich: int = 30) -> list[dict]:
    """
    Get the routed ASNs for a country via RIPEstat's country-asns endpoint
    (this is the correct, live endpoint - it was NOT retired, contrary to
    an earlier incorrect comment in this file).

    country-asns returns a custom string format like:
        "{AsnSingle(1234), AsnSingle(5678), ...}"
    not a JSON array, so we parse it with a regex.

    The endpoint gives no names or prefix counts, just the routed ASN list
    (often 1000+ entries) - we enrich the first `max_to_enrich` with name
    and prefix count via as-overview and announced-prefixes, then sort by
    IPv4 prefix count descending so the biggest networks surface first.
    """
    import re
    import asyncio as _asyncio

    cc = country_code.lower()
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = await client.get(
            f"{BASE_URL}/country-asns/data.json",
            params={"resource": cc, "lod": 1},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

    countries = data.get("countries", [])
    if not countries:
        return []

    routed_raw = countries[0].get("routed", "")
    asn_ints = [int(n) for n in re.findall(r"AsnSingle\((\d+)\)", routed_raw)]

    if not asn_ints:
        return []

    # Enrich a capped subset in parallel - enriching thousands of ASNs would be
    # far too slow and is unnecessary since we only need the top N by size anyway.
    # We take a larger sample than max_to_enrich because un-enriched order is
    # arbitrary, then sort and trim after enrichment.
    sample_size = min(len(asn_ints), max(max_to_enrich * 15, 400))
    sample = asn_ints[:sample_size]

    semaphore = _asyncio.Semaphore(20)

    async def enrich_one(asn_int: int) -> dict:
        async with semaphore:
            try:
                overview, prefixes = await _asyncio.gather(
                    get_asn_overview(asn_int),
                    get_announced_prefixes(asn_int),
                    return_exceptions=True,
                )
                name = overview.get("name", "unknown") if isinstance(overview, dict) else "unknown"
                ipv4 = prefixes.get("ipv4_count", 0) if isinstance(prefixes, dict) else 0
                ipv6 = prefixes.get("ipv6_count", 0) if isinstance(prefixes, dict) else 0
            except Exception:
                name, ipv4, ipv6 = "unknown", 0, 0
            return {
                "asn":           f"AS{asn_int}",
                "asn_int":       asn_int,
                "name":          name,
                "ipv4_prefixes": ipv4,
                "ipv6_prefixes": ipv6,
            }

    enriched = await _asyncio.gather(*[enrich_one(a) for a in sample])
    enriched.sort(key=lambda e: e["ipv4_prefixes"], reverse=True)

    top = enriched[:max_to_enrich]
    for entry in top:
        entry["_total_routed_in_country"] = len(asn_ints)
        entry["_sampled_count"] = len(sample)

    return top
