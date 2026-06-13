"""
PeeringDB Connector
===================
PeeringDB (https://www.peeringdb.com) is the industry-standard database
for network interconnection data. It is FREE and openly accessible.

What it contains:
  - Every major network's ASN(s)
  - Which Internet Exchanges (IXPs) they are present at
  - Their peering policy (open / selective / closed)
  - Contacts for peering and NOC
  - Data centers / facilities they colocate in

This connector talks to the PeeringDB REST API.
API docs: https://www.peeringdb.com/apidocs/
"""

import os
import httpx                        # Like "requests" but async (handles multiple calls at once)
from typing import Any


# ── Constants ──────────────────────────────────────────────────────────────

BASE_URL = "https://www.peeringdb.com/api"

# How long to wait for PeeringDB before giving up (seconds)
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))


# ── Helpers ────────────────────────────────────────────────────────────────

def _headers() -> dict:
    """
    Build HTTP headers for PeeringDB requests.
    If the user has an API key in .env, we include it.
    Without a key the API still works, just with lower rate limits.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "gtiti/0.1 (contact@gtiti.io)",
    }
    api_key = os.getenv("PEERINGDB_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Api-Key {api_key}"
    return headers


def _clean(value: Any, fallback: str = "unknown") -> str:
    """
    PeeringDB sometimes returns None or empty strings.
    This turns them into a readable fallback word.
    """
    if value is None or str(value).strip() == "":
        return fallback
    return str(value).strip()


# ── Main functions ─────────────────────────────────────────────────────────

async def search_networks(query: str) -> list[dict]:
    """
    Search PeeringDB for networks matching a name or ASN.

    Example:
        search_networks("Deutsche Telekom")
        search_networks("AS3320")
        search_networks("Vivo")

    Returns a list of matching network records, each containing:
        - id         : PeeringDB internal ID (used in other API calls)
        - name       : Full name of the network
        - aka        : Also-known-as / trading name
        - asn        : Primary AS number
        - info_type  : "NSP" (carrier), "Content", "Cable/DSL/ISP", etc.
        - policy     : Peering policy (Open / Selective / Closed)
        - website    : Company website
        - notes      : Free-text notes from the operator
    """
    # If the query looks like "AS12345" or just "12345", search by ASN directly
    asn_query = query.upper().replace("AS", "").strip()
    if asn_query.isdigit():
        params = {"asn": asn_query, "depth": 2}
    else:
        # Text search — PeeringDB matches against name and aka fields
        params = {"name__icontains": query, "depth": 0}

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{BASE_URL}/net",
            params=params,
            headers=_headers(),
        )
        resp.raise_for_status()     # Raises an error if status code is 4xx/5xx
        data = resp.json()

    networks = []
    for net in data.get("data", []):
        networks.append({
            "peeringdb_id": net.get("id"),
            "name":         _clean(net.get("name")),
            "aka":          _clean(net.get("aka"), ""),
            "asn":          net.get("asn"),
            "type":         _clean(net.get("info_type"), "NSP"),
            "peering_policy": _clean(net.get("policy_general"), "unknown"),
            "website":      _clean(net.get("website"), ""),
            "notes":        _clean(net.get("notes"), ""),
        })

    return networks


async def get_network_details(peeringdb_id: int) -> dict:
    """
    Given a PeeringDB network ID (from search_networks above),
    fetch the full record including ALL ASNs, IXP presence, and contacts.

    This is a "depth=2" call — PeeringDB nests related objects inside the response
    so we get everything in one HTTP request instead of many.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(
            f"{BASE_URL}/net/{peeringdb_id}",
            params={"depth": 2},
            headers=_headers(),
        )
        resp.raise_for_status()
        data = resp.json()

    net = data.get("data", [{}])[0] if data.get("data") else {}

    # ── Extract IXP presence ───────────────────────────────────────────────
    # "netixlan_set" = the list of IX LANs this network is present at
    ixp_presence = []
    for ixlan in net.get("netixlan_set", []):
        ixp_presence.append({
            "ixp_name":  _clean(ixlan.get("name")),
            "speed_mbps": ixlan.get("speed", 0),
            "speed_human": _mbps_to_human(ixlan.get("speed", 0)),
            "ipv4":      _clean(ixlan.get("ipaddr4"), ""),
            "ipv6":      _clean(ixlan.get("ipaddr6"), ""),
            "is_rs_peer": ixlan.get("is_rs_peer", False),  # Connected to route server?
        })

    # Sort IXPs by speed descending — biggest ports first
    ixp_presence.sort(key=lambda x: x["speed_mbps"], reverse=True)

    # ── Extract contacts ───────────────────────────────────────────────────
    # "poc_set" = Points of Contact
    contacts = []
    for poc in net.get("poc_set", []):
        role = _clean(poc.get("role"), "")
        # Only include roles relevant to wholesale/peering/NOC
        if any(r in role.upper() for r in ["PEERING", "NOC", "SALES", "TECHNICAL", "POLICY"]):
            contacts.append({
                "name":  _clean(poc.get("name"), "Contact"),
                "role":  role,
                "email": _clean(poc.get("email"), ""),
                "phone": _clean(poc.get("phone"), ""),
                "visible": _clean(poc.get("visible"), "Public"),
            })

    # ── Extract facility presence ──────────────────────────────────────────
    # "netfac_set" = data centers / facilities this network is present in
    facilities = []
    for fac in net.get("netfac_set", []):
        facilities.append({
            "name":    _clean(fac.get("name")),
            "city":    _clean(fac.get("city"), ""),
            "country": _clean(fac.get("country"), ""),
        })

    return {
        "peeringdb_id":   net.get("id"),
        "name":           _clean(net.get("name")),
        "aka":            _clean(net.get("aka"), ""),
        "asn":            net.get("asn"),
        "website":        _clean(net.get("website"), ""),
        "type":           _clean(net.get("info_type"), "NSP"),
        "peering_policy": _clean(net.get("policy_general"), "unknown"),
        "peering_policy_url": _clean(net.get("policy_url"), ""),
        "irr_as_set":     _clean(net.get("irr_as_set"), ""),  # BGP filter object
        "info_prefixes4": net.get("info_prefixes4", 0),       # Approx IPv4 prefixes advertised
        "info_prefixes6": net.get("info_prefixes6", 0),       # Approx IPv6 prefixes advertised
        "ixp_presence":   ixp_presence,
        "contacts":       contacts,
        "facilities":     facilities,
        "looking_glass":  _clean(net.get("looking_glass"), ""),
        "route_server":   _clean(net.get("route_server"), ""),
        "peeringdb_url":  f"https://www.peeringdb.com/net/{net.get('id')}",
    }


async def get_ixp_members(ixp_name: str) -> list[dict]:
    """
    Look up an Internet Exchange by name and return all its member networks.
    Useful for: "who else is present at DE-CIX Frankfurt?"

    Returns a list of networks present at that IX.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # First, find the IXP record
        resp = await client.get(
            f"{BASE_URL}/ix",
            params={"name__icontains": ixp_name, "depth": 0},
            headers=_headers(),
        )
        resp.raise_for_status()
        ix_data = resp.json()

    results = []
    for ix in ix_data.get("data", [])[:3]:   # Limit to top 3 matching IXPs
        # Now get the networks connected to this IXP
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(
                f"{BASE_URL}/netixlan",
                params={"ixlan_id": ix.get("id"), "depth": 1},
                headers=_headers(),
            )
            resp.raise_for_status()
            member_data = resp.json()

        members = []
        for m in member_data.get("data", [])[:50]:   # Cap at 50 members
            members.append({
                "network_name": _clean(m.get("name")),
                "asn":          m.get("asn"),
                "speed_human":  _mbps_to_human(m.get("speed", 0)),
                "is_rs_peer":   m.get("is_rs_peer", False),
            })

        results.append({
            "ixp_name":    _clean(ix.get("name")),
            "city":        _clean(ix.get("city"), ""),
            "country":     _clean(ix.get("country"), ""),
            "members":     members,
            "member_count": len(members),
            "peeringdb_url": f"https://www.peeringdb.com/ix/{ix.get('id')}",
        })

    return results


# ── Utility ────────────────────────────────────────────────────────────────

def _mbps_to_human(mbps: int) -> str:
    """
    Convert a speed in Mbps to a human-readable string.
    Examples:  1000 → "1G",  100000 → "100G",  400000 → "400G"
    """
    if not mbps:
        return "unknown"
    if mbps >= 1_000_000:
        return f"{mbps // 1_000_000}T"
    if mbps >= 1_000:
        return f"{mbps // 1_000}G"
    return f"{mbps}M"
