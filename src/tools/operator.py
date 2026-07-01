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
from src.connectors import caida


# ── Name disambiguation ────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Lowercase, strip punctuation and common suffixes for comparison."""
    text = text.lower()
    text = re.sub(r"\b(ab|ag|as|asn|bv|co|corp|gmbh|inc|ltd|llc|nv|oy|plc|sa|srl|telekom|communications|networks|telecom|group|sverige|sweden|germany|brazil|italia|france|spain)\b", "", text)
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return text.strip()


# Country name → ISO code mapping for geographic disambiguation
_COUNTRY_HINTS = {
    "romania": "RO", "romanian": "RO",
    "spain": "ES", "spanish": "ES",
    "germany": "DE", "german": "DE",
    "france": "FR", "french": "FR",
    "italy": "IT", "italian": "IT",
    "netherlands": "NL", "dutch": "NL",
    "sweden": "SE", "swedish": "SE",
    "norway": "NO", "norwegian": "NO",
    "denmark": "DK", "danish": "DK",
    "finland": "FI", "finnish": "FI",
    "poland": "PL", "polish": "PL",
    "hungary": "HU", "hungarian": "HU",
    "portugal": "PT", "portuguese": "PT",
    "brazil": "BR", "brazilian": "BR",
    "nigeria": "NG", "nigerian": "NG",
    "japan": "JP", "japanese": "JP",
    "india": "IN", "indian": "IN",
    "uk": "GB", "united kingdom": "GB", "britain": "GB", "british": "GB",
    "us": "US", "usa": "US", "united states": "US", "american": "US",
    "australia": "AU", "australian": "AU",
    "canada": "CA", "canadian": "CA",
    "china": "CN", "chinese": "CN",
    "singapore": "SG",
    "south africa": "ZA",
}

def _extract_country_hint(query: str) -> str | None:
    """Extract a country ISO code from a query string, if any country name is present."""
    q_lower = query.lower()
    # Check multi-word country names first (e.g. "united kingdom" before "kingdom")
    for name, iso in sorted(_COUNTRY_HINTS.items(), key=lambda x: -len(x[0])):
        if name in q_lower:
            return iso
    # Also check bare 2-letter ISO codes at word boundaries (e.g. "DIGI RO")
    import re as _re
    m = _re.search(r"\b([A-Z]{2})\b", query)
    if m and m.group(1) in set(_COUNTRY_HINTS.values()):
        return m.group(1)
    return None


def _score_match(query: str, result: dict, country_hint: str | None = None) -> int:
    """
    Score a PeeringDB result against the query.
    Higher = better match. Used to pick the best result from the list.
    Includes geographic boosting when a country hint is present in the query.
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

    # Geographic boost: if the query mentions a country, prefer results
    # whose name/aka contains the country ISO code or country name
    if country_hint:
        name_raw = (result.get("name", "") + " " + result.get("aka", "")).upper()
        scope = result.get("info_scope", "").upper()
        if country_hint in name_raw:
            score += 60
        # info_scope is continent-level (e.g. "Europe") not country-specific,
        # but it's still a weak signal - don't use it for boosting, only for
        # penalizing clearly wrong-continent results in future if needed

    # Penalize for missing significant query words — if query has multiple words
    # but only some appear in the name, the missing ones are a negative signal.
    # This catches "RCS RDS" matching "RCS Networks" (RDS is missing entirely).
    _GENERIC = {"networks", "communications", "telecom", "telecommunications",
                "internet", "services", "systems", "technology", "technologies",
                "group", "global", "international", "solutions", "holdings"}
    significant_q_words = q_words - _GENERIC
    if len(significant_q_words) > 1:
        missing = significant_q_words - name_words - aka_words
        score -= len(missing) * 25

    return score


def _best_match(query: str, results: list[dict]) -> tuple[dict, list[dict]]:
    """
    Return (best_match, alternative_candidates) where alternatives are other
    results with similar scores that could be the intended target.
    Falls back to index 0 if nothing scores positively.
    """
    # If query looks like an ASN (AS1257, 1257), trust PeeringDB ordering
    if re.match(r"^(as)?\d+$", query.strip(), re.IGNORECASE):
        return results[0], []

    country_hint = _extract_country_hint(query)
    scored = [(r, _score_match(query, r, country_hint=country_hint)) for r in results]
    scored.sort(key=lambda x: x[1], reverse=True)

    best, best_score = scored[0]

    # Surface alternatives: other results within 30 points of the best score
    # These are candidates the user might have intended instead
    alternatives = [
        r for r, s in scored[1:]
        if s >= best_score - 30 and r.get("peeringdb_id") != best.get("peeringdb_id")
    ][:3]  # cap at 3 alternatives

    return best, alternatives


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
    best_match, alternative_matches = _best_match(query, pdb_results)
    pdb_id      = best_match["peeringdb_id"]
    primary_asn = best_match["asn"]

    # Detect low-confidence matches — when the best score is very low,
    # we likely matched something wrong rather than the intended operator.
    country_hint = _extract_country_hint(query)
    best_score = _score_match(query, best_match, country_hint=country_hint)
    low_confidence = best_score < 30

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
        "ixp_note": (
            "No IXP LAN entries found in PeeringDB. This may reflect stale or missing "
            "PeeringDB records rather than actual absence from exchanges. "
            "Use gtiti_ixpdb_lookup for an independent cross-check via Euro-IX."
            if not pdb_detail.get("ixp_presence") else None
        ),

        # Contacts
        "contacts": pdb_detail.get("contacts", []),

        # Facilities
        "facility_count": len(pdb_detail.get("facilities", [])),
        "facilities":     pdb_detail.get("facilities", []),

        # Upstream providers
        "upstreams":   ripe_upstreams.get("upstreams", []),
        "peers_count": len(ripe_upstreams.get("peers", [])),

        # Disambiguation — alternatives with similar scores to the selected match
        "disambiguation_warning": (
            f"LOW CONFIDENCE MATCH: '{query}' did not match any network name clearly. "
            f"Returned '{best_match['name']}' (AS{primary_asn}) as the closest result, "
            f"but this is likely wrong. Try querying by ASN directly (e.g. 'AS8708') "
            f"or use the official PeeringDB network name."
            if low_confidence else (
            f"Multiple similarly-named networks found. Matched '{best_match['name']}' "
            f"(AS{primary_asn}). If this is wrong, try querying by ASN directly "
            f"or include the country name (e.g. 'DIGI Romania')."
            if alternative_matches else None
            )
        ),
        "alternative_matches": [
            {"name": r["name"], "asn": r["asn"], "type": r.get("type", "")}
            for r in alternative_matches
        ],

        # Source links
        "peeringdb_url": pdb_detail.get("peeringdb_url", ""),
        "data_sources":  ["PeeringDB", "RIPE NCC RIPEstat"],
    }


# ── Tool: Look up operators in a country ──────────────────────────────────

async def _enrich_with_peeringdb(entries: list[dict]) -> list[dict]:
    """Cross-check a bounded list of ASN entries against PeeringDB for peering
    policy / PeeringDB ID, bounded concurrency so we don't hammer PeeringDB."""
    semaphore = asyncio.Semaphore(5)

    async def enrich_one(entry: dict) -> dict:
        async with semaphore:
            try:
                pdb_results = await peeringdb.search_networks(str(entry["asn_int"]))
                if pdb_results:
                    pdb = pdb_results[0]
                    return {
                        **entry,
                        "peering_policy": pdb.get("peering_policy", "unknown"),
                        "peeringdb_id":   pdb.get("peeringdb_id"),
                        "in_peeringdb":   True,
                    }
            except Exception:
                pass
            return {**entry, "in_peeringdb": False, "peering_policy": "unknown"}

    return list(await asyncio.gather(*[enrich_one(e) for e in entries]))


async def _operators_in_country_ripestat_fallback(country_code: str, top_n: int) -> dict:
    """
    Older approach, kept as a fallback for when CAIDA AS Rank is unreachable.
    RIPEstat's country-asns list is NOT sorted by size — this enriches a
    bounded sample and sorts by IPv4 prefix count after the fact, so very
    large countries may have bigger operators outside the sampled range.
    """
    country_asns = await ripe.get_country_asns(country_code)

    if not country_asns:
        return {
            "status":  "not_found",
            "message": f"No routed ASNs found for country code '{country_code}'. "
                       f"Make sure you're using the 2-letter ISO code (DE, BR, JP...)",
            "country": country_code,
        }

    true_total_routed = country_asns[0].get("_total_routed_in_country", len(country_asns)) if country_asns else 0
    enriched = await _enrich_with_peeringdb(country_asns[:top_n])

    return {
        "status":            "ok",
        "country":           country_code.upper(),
        "total_asns_routed": true_total_routed,
        "operators":         enriched,
        "data_sources":      ["RIPE NCC RIPEstat", "PeeringDB"],
        "note": (
            f"CAIDA AS Rank was unreachable, so this fell back to RIPEstat sampling. "
            f"Showing top {len(enriched)} networks by IPv4 prefix count, sampled from "
            f"{true_total_routed} routed ASNs in {country_code.upper()}. "
            f"IMPORTANT CAVEAT: RIPEstat's country-asns list is not sorted by size, "
            f"and only a bounded sample is enriched for speed - very large countries "
            f"may have bigger operators outside the sampled range that aren't shown here. "
            f"For a definitive answer on a specific known operator, use gtiti_operator_lookup "
            f"with the operator's name directly instead."
        ),
    }


async def operators_in_country(country_code: str, top_n: int = 10) -> dict:
    """
    Find the most significant telecom operators in a given country, ranked by
    CAIDA's global AS Rank (customer cone / transit degree significance).

    CAIDA's bulk ASN query has no country filter, but it IS globally sorted
    by rank by default (confirmed live against the schema), so we paginate
    that rank-ordered list and keep whatever matches the target country. This
    is deterministic and surfaces truly significant operators first, unlike
    the old RIPEstat-sampling approach (kept as a fallback below) which drew
    from an arbitrary-order ASN list and could miss bigger operators outside
    the sampled range.

    NOTE: this ranks by CAIDA's topological significance, not raw IPv4 prefix
    count — a different (arguably more meaningful, for "important operator")
    metric than what this tool used to report. See the "note" field.
    """
    country_code = country_code.upper()
    caida_result = await caida.get_top_asns_by_country(country_code, top_n=top_n)

    if caida_result.get("error"):
        return await _operators_in_country_ripestat_fallback(country_code, top_n)

    matches = caida_result.get("matches", [])
    if not matches:
        return {
            "status":  "not_found",
            "message": f"No ASNs found for country code '{country_code}' within CAIDA's top "
                       f"{caida_result.get('scanned_globally_ranked_asns', 0)} globally-ranked ASNs. "
                       f"Make sure you're using the 2-letter ISO code (DE, BR, JP...) — if the code is "
                       f"correct, this country's operators may simply rank very low globally; try "
                       f"gtiti_operator_lookup for a specific known operator instead.",
            "country": country_code,
        }

    enriched = await _enrich_with_peeringdb(matches)

    return {
        "status":       "ok",
        "country":      country_code,
        "operators":    enriched,
        "data_sources": ["CAIDA AS Rank (global rank-ordered scan)", "PeeringDB"],
        "note": (
            f"Ranked by CAIDA's global AS significance (customer cone size / transit degree), "
            f"scanning the top {caida_result['scanned_globally_ranked_asns']} globally-ranked ASNs "
            f"(out of {caida_result.get('total_asns_in_dataset', 'unknown')} total) and keeping the "
            f"ones registered in {country_code}. This is deterministic, unlike the previous approach "
            f"(arbitrary-order sampling from RIPEstat). METRIC CHANGE: this ranks by topological "
            f"significance, not raw IPv4 prefix count — a content network with many small disaggregated "
            f"prefixes may rank differently than before. For a definitive answer on a specific known "
            f"operator, use gtiti_operator_lookup with the operator's name directly instead."
        ),
        "truncated_scan": caida_result.get("truncated_scan", False),
        "truncated_scan_note": (
            f"Only found {len(matches)} of the requested {top_n} within the scan limit — this country's "
            f"lower-ranked operators may not be globally significant enough to appear in a bounded scan."
            if caida_result.get("truncated_scan") else None
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
