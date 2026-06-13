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

import asyncio
import re
from typing import Any

from src.connectors import peeringdb
from src.connectors import ripe


# ── Name disambiguation ────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation and common suffixes for comparison."""
    text = text.lower()
    text = re.sub(r"\b(ab|ag|as|asn|bv|co|corp|gmbh|inc|ltd|llc|nv|oy|plc|sa|srl|telekom|communications|networks|telecom|group|sverige|sweden|germany|brazil|italia|france|spain)\b", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return text.strip()


def _score_match(query: str, result: dict) -> int:
    """
    Score a PeeringDB result against the query.
    Higher = better match. Used to pick the best result from the list.
    """
    q = _normalize(query)
    name = _normalize(result.get("name", ""))
    aka  = _normalize(result.get("aka", ""))

    score = 0

    # Exact match on normalized name — strongest signal
    if q == name:
        score += 100

    # Query is fully contained in name or vice versa
    if q in name or name in q:
        score += 50

    # Check aka field too
    if q in aka or aka in q:
        score += 40

    # Word overlap between query and name
    q_words    = set(q.split())
    name_words = set(name.split())
    aka_words  = set(aka.split())
    overlap_name = q_words & name_words
    overlap_aka  = q_words & aka_words
    score += len(overlap_name) * 15
    score += len(overlap_aka)  * 10

    # Penalize if query words don't appear at all
    if not overlap_name and not overlap_aka:
        score -= 30

    return score


def _best_match(query: str, results: list[dict]) -> dict:
    """
    Return the result that best matches the query by name similarity.
    Falls back to index 0 if nothing scores positively.
    """
    # If query looks like an ASN (AS1257, 1257), trust PeeringDB ordering
    if re.match(r"^(as)?\d+$", query.strip(), re.IGNORECASE):
        return results[0]

    scored = [(r, _score_match(query, r)) for r in results]
    scored.sort(key=lambda x: x[1], reverse=True)

    best, best_score = scored[0]

    # If best score is very low, nothing really matched — still return it
    # but the caller will surface other_matches so the user can correct
    return best


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
                       f"Try the ASN directly (e.g. 'AS1257') or the official "
                       f"network name from https://www.peeringdb.com",
            "query":   query,
        }

    # Pick the best match by name similarity instead of blindly taking index 0
    best_match  = _best_match(query, pdb_results)
    pdb_id      = best_match["peeringdb_id"]
    primary_asn = best_match["asn"]

    # ── Step 2: Fire all enrichment calls IN PARALLEL ──────────────────────
    pdb_detail_task    = peeringdb.get_network_details(pdb_id)
    ripe_overview_task  = ripe.get_asn_overview(primary_asn)
    ripe_prefixes_task  = ripe.get_announced_prefixes(primary_asn)
    ripe_upstreams_task = ripe.get_upstreams(primary_asn)

    results = await asyncio.gather(
        pdb_detail_task,
        ripe_overview_task,
        ripe_prefixes_task,
        ripe_upstreams_task,
        return_exceptions=True,
    )

    pdb_detail, ripe_overview, ripe_prefixes, ripe_upstreams = results

    # ── Step 3: Handle partial failures gracefully ─────────────────────────
    def safe(result: Any, fallback: Any = {}) -> Any:
        if isinstance(result, Exception):
            return fallback
        return result

    pdb_detail     = safe(pdb_detail, {})
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
        "primary_asn": f"AS{primary_asn}",
        "irr_as_set":  pdb_detail.get("irr_as_set", ""),

        # Routing data (from RIPE)
        "ipv4_prefixes_announced": ripe_prefixes.get("ipv4_count", 0),
        "ipv6_prefixes_announced": ripe_prefixes.get("ipv6_count", 0),
        "sample_prefixes_ipv4":    ripe_prefixes.get("ipv4_prefixes", [])[:5],

        # Peering
        "peering_policy":     pdb_detail.get("peering_policy", "unknown"),
        "peering_policy_url": pdb_detail.get("peering_policy_url", ""),
        "looking_glass":      pdb_detail.get("looking_glass", ""),

        # IXP presence
        "ixp_count":    len(pdb_detail.get("ixp_presence", [])),
        "ixp_presence": pdb_detail.get("ixp_presence", []),

        # Contacts
        "contacts": pdb_detail.get("contacts", []),

        # Facilities
        "facility_count": len(pdb_detail.get("facilities", [])),
        "facilities":     pdb_detail.get("facilities", []),

        # Upstream providers
        "upstreams":   ripe_upstreams.get("upstreams", []),
        "peers_count": len(ripe_upstreams.get("peers", [])),

        # Other matches — so user can see alternatives if the pick was wrong
        "other_matches": [
            {"name": r["name"], "asn": r["asn"], "type": r["type"]}
            for r in pdb_results
            if r["peeringdb_id"] != pdb_id
        ][:4],

        # Source links
        "peeringdb_url": pdb_detail.get("peeringdb_url", ""),
        "data_sources":  ["PeeringDB", "RIPE NCC RIPEstat"],
    }


# ── Tool: Look up operators in a country ──────────────────────────────────

async def operators_in_country(country_code: str, top_n: int = 10) -> dict:
    """
    Find the most significant telecom operators in a given country.
    """
    country_asns = await ripe.get_country_asns(country_code)

    if not country_asns:
        return {
            "status":  "not_found",
            "message": f"No routed ASNs found for country code '{country_code}'. "
                       f"Make sure you're using the 2-letter ISO code (DE, BR, JP...)",
            "country": country_code,
        }

    top_asns = country_asns[:top_n]

    async def enrich_asn(asn_entry: dict) -> dict:
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

    semaphore = asyncio.Semaphore(5)

    async def guarded_enrich(asn_entry):
        async with semaphore:
            return await enrich_asn(asn_entry)

    enriched = await asyncio.gather(*[guarded_enrich(a) for a in top_asns])

    return {
        "status":            "ok",
        "country":           country_code.upper(),
        "total_asns_routed": len(country_asns),
        "operators":         list(enriched),
        "data_sources":      ["RIPE NCC RIPEstat", "PeeringDB"],
        "note": (
            f"Showing top {len(enriched)} networks by IPv4 prefix count. "
            f"Prefix count approximates network size but is not exact."
        ),
    }


# ── Tool: Look up an IXP ──────────────────────────────────────────────────

async def lookup_ixp(ixp_name: str) -> dict:
    """
    Find an Internet Exchange Point and list its members.
    """
    members = await peeringdb.get_ixp_members(ixp_name)

    if not members:
        return {
            "status":  "not_found",
            "message": f"No IXP found matching '{ixp_name}'. "
                       f"Try a partial name like 'DE-CIX' or 'LINX'.",
        }

    return {
        "status":       "ok",
        "query":        ixp_name,
        "results":      members,
        "data_sources": ["PeeringDB"],
    }
