"""
Operator Intelligence Tool
===========================
This is the TOOL that Claude calls when someone asks about a telecom operator.

Think of it like this:
  - The connectors (peeringdb.py, ripe.py) are specialists who each know one thing.
  - This file is the COORDINATOR who sends them all to work at the same time,
    collects their answers, and assembles one clean briefing.

Claude will call the functions here. It doesn't know or care about the
connectors — it just calls these functions and gets structured data back.
"""

import asyncio                          # Lets us run multiple API calls at the same time
from typing import Any

from src.connectors import peeringdb
from src.connectors import ripe


# ── Tool: Look up a single operator ───────────────────────────────────────

async def lookup_operator(query: str) -> dict:
    """
    Full intelligence briefing for a telecom operator.

    Input:  operator name ("Deutsche Telekom"), ASN ("AS3320"), or
            a combined query ("Vivo Brazil")

    Output: a structured dict covering:
        - Basic info (name, HQ country, type)
        - All ASNs
        - IXP presence
        - Peering contacts
        - BGP prefix counts (from RIPE)
        - Upstream providers
        - Link to full PeeringDB record

    This function fires PeeringDB and RIPE lookups IN PARALLEL using asyncio,
    so total time is ~max(peeringdb_time, ripe_time) — not the sum.
    """

    # ── Step 1: Search PeeringDB for matching networks ─────────────────────
    pdb_results = await peeringdb.search_networks(query)

    if not pdb_results:
        return {
            "status":  "not_found",
            "message": f"No networks found in PeeringDB matching '{query}'. "
                       f"Try the ASN directly (e.g. 'AS3320') or the official "
                       f"network name from https://www.peeringdb.com",
            "query":   query,
        }

    # Take the best match (first result — PeeringDB returns most relevant first)
    best_match = pdb_results[0]
    pdb_id     = best_match["peeringdb_id"]
    primary_asn = best_match["asn"]

    # ── Step 2: Fire all enrichment calls IN PARALLEL ──────────────────────
    # asyncio.gather runs all coroutines concurrently — like opening 3 browser
    # tabs at the same time instead of one after another.
    pdb_detail_task   = peeringdb.get_network_details(pdb_id)
    ripe_overview_task = ripe.get_asn_overview(primary_asn)
    ripe_prefixes_task = ripe.get_announced_prefixes(primary_asn)
    ripe_upstreams_task = ripe.get_upstreams(primary_asn)

    # Run all 4 tasks simultaneously
    results = await asyncio.gather(
        pdb_detail_task,
        ripe_overview_task,
        ripe_prefixes_task,
        ripe_upstreams_task,
        return_exceptions=True,   # If one fails, others still succeed
    )

    pdb_detail, ripe_overview, ripe_prefixes, ripe_upstreams = results

    # ── Step 3: Handle partial failures gracefully ─────────────────────────
    # If RIPE is down or the ASN isn't in their database, we still return
    # what we have from PeeringDB.
    def safe(result: Any, fallback: Any = {}) -> Any:
        if isinstance(result, Exception):
            return fallback
        return result

    pdb_detail    = safe(pdb_detail, {})
    ripe_overview  = safe(ripe_overview, {})
    ripe_prefixes  = safe(ripe_prefixes, {})
    ripe_upstreams = safe(ripe_upstreams, {})

    # ── Step 4: Assemble the unified response ──────────────────────────────
    return {
        "status": "ok",

        # Identity
        "name":    pdb_detail.get("name", best_match["name"]),
        "aka":     pdb_detail.get("aka", ""),
        "website": pdb_detail.get("website", ""),
        "type":    pdb_detail.get("type", "NSP"),

        # ASN info
        "primary_asn":    f"AS{primary_asn}",
        "irr_as_set":     pdb_detail.get("irr_as_set", ""),   # IRR filter object

        # Routing data (from RIPE)
        "ipv4_prefixes_announced": ripe_prefixes.get("ipv4_count", 0),
        "ipv6_prefixes_announced": ripe_prefixes.get("ipv6_count", 0),
        "sample_prefixes_ipv4":    ripe_prefixes.get("ipv4_prefixes", [])[:5],

        # Peering
        "peering_policy":     pdb_detail.get("peering_policy", "unknown"),
        "peering_policy_url": pdb_detail.get("peering_policy_url", ""),
        "looking_glass":      pdb_detail.get("looking_glass", ""),

        # IXP presence — sorted by speed (biggest ports first)
        "ixp_count":    len(pdb_detail.get("ixp_presence", [])),
        "ixp_presence": pdb_detail.get("ixp_presence", []),

        # Contacts
        "contacts": pdb_detail.get("contacts", []),

        # Facilities / colo
        "facility_count": len(pdb_detail.get("facilities", [])),
        "facilities":     pdb_detail.get("facilities", []),

        # Upstream providers (who they buy transit from)
        "upstreams":   ripe_upstreams.get("upstreams", []),
        "peers_count": len(ripe_upstreams.get("peers", [])),

        # Other matches from the search (in case user wants a different one)
        "other_matches": [
            {"name": r["name"], "asn": r["asn"], "type": r["type"]}
            for r in pdb_results[1:4]    # Up to 3 alternatives
        ],

        # Source links
        "peeringdb_url": pdb_detail.get("peeringdb_url", ""),
        "data_sources":  ["PeeringDB", "RIPE NCC RIPEstat"],
    }


# ── Tool: Look up operators in a country ──────────────────────────────────

async def operators_in_country(country_code: str, top_n: int = 10) -> dict:
    """
    Find the most significant telecom operators in a given country.

    Answers the question: "Who are the most important telecom operators in X?"

    Strategy:
      1. Ask RIPE for all routed ASNs in the country
      2. Take the top N by prefix count (prefix count ≈ network size)
      3. For each, do a quick PeeringDB lookup to get the operator name
         and peering policy

    country_code: ISO 2-letter code (DE, BR, JP, NG, IN, US, FR...)
    top_n:        How many operators to return (default 10)
    """

    # Step 1: Get all ASNs in the country from RIPE
    country_asns = await ripe.get_country_asns(country_code)

    if not country_asns:
        return {
            "status":  "not_found",
            "message": f"No routed ASNs found for country code '{country_code}'. "
                       f"Make sure you're using the 2-letter ISO code (DE, BR, JP...)",
            "country": country_code,
        }

    # Take the biggest N ASNs
    top_asns = country_asns[:top_n]

    # Step 2: For each ASN, try to find its PeeringDB record in parallel
    async def enrich_asn(asn_entry: dict) -> dict:
        """Try to enrich one ASN with PeeringDB data."""
        try:
            pdb_results = await peeringdb.search_networks(str(asn_entry["asn_int"]))
            if pdb_results:
                pdb = pdb_results[0]
                return {
                    **asn_entry,
                    "peering_policy": pdb.get("peering_policy", "unknown"),
                    "peeringdb_id":   pdb.get("peeringdb_id"),
                    "in_peeringdb":   True,
                }
        except Exception:
            pass
        return {**asn_entry, "in_peeringdb": False, "peering_policy": "unknown"}

    # Run all enrichments in parallel (but cap concurrency to be polite to APIs)
    semaphore = asyncio.Semaphore(5)   # Max 5 simultaneous requests

    async def guarded_enrich(asn_entry):
        async with semaphore:
            return await enrich_asn(asn_entry)

    enriched = await asyncio.gather(*[guarded_enrich(a) for a in top_asns])

    return {
        "status":        "ok",
        "country":       country_code.upper(),
        "total_asns_routed": len(country_asns),
        "operators":     list(enriched),
        "data_sources":  ["RIPE NCC RIPEstat", "PeeringDB"],
        "note": (
            f"Showing top {len(enriched)} networks by IPv4 prefix count. "
            f"Prefix count approximates network size but is not exact."
        ),
    }


# ── Tool: Look up an IXP ──────────────────────────────────────────────────

async def lookup_ixp(ixp_name: str) -> dict:
    """
    Find an Internet Exchange Point and list its members.

    Answers: "Who is present at DE-CIX Frankfurt?"
             "What networks peer at LINX?"

    Returns the IXP details and up to 50 member networks.
    """
    members = await peeringdb.get_ixp_members(ixp_name)

    if not members:
        return {
            "status":  "not_found",
            "message": f"No IXP found matching '{ixp_name}'. "
                       f"Try a partial name like 'DE-CIX' or 'LINX'.",
        }

    return {
        "status":      "ok",
        "query":       ixp_name,
        "results":     members,
        "data_sources": ["PeeringDB"],
    }
