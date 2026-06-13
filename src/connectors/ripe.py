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


async def get_country_asns(country_code: str) -> list[dict]:
    """
    Get major ASNs in a country using the routing-status endpoint.
    
    RIPE retired country-asns in 2025. We now use ris-asns which 
    returns ASNs seen in BGP for a given country's prefix space.
    """
    cc = country_code.upper()
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
        resp = await client.get(
            f"{BASE_URL}/ris-asns/data.json",
            params={"list_asns": "true", "asn_types": "o", "resource": cc},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})

    # ris-asns returns {"counts": {"originating": N}, "asns": {"originating": [...]}}
    asn_list = (
        data.get("asns", {}).get("originating", [])
        or data.get("asns", {}).get("transiting", [])
        or []
    )

    results = []
    for entry in asn_list:
        # entry can be just an int ASN, or a dict
        if isinstance(entry, int):
            results.append({
                "asn":           f"AS{entry}",
                "asn_int":       entry,
                "name":          "unknown",
                "ipv4_prefixes": 0,
                "ipv6_prefixes": 0,
            })
        elif isinstance(entry, dict):
            asn = entry.get("asn") or entry.get("id")
            results.append({
                "asn":           f"AS{asn}",
                "asn_int":       int(asn),
                "name":          entry.get("holder") or entry.get("name") or "unknown",
                "ipv4_prefixes": entry.get("ipv4_prefixes") or entry.get("pfxs", {}).get("v4", 0),
                "ipv6_prefixes": entry.get("ipv6_prefixes") or entry.get("pfxs", {}).get("v6", 0),
            })

    return results[:30]
