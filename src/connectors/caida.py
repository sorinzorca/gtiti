import asyncio
import aiohttp

CAIDA_GRAPHQL_URL = "https://api.asrank.caida.org/v2/graphql"

def _parse_asn(asn):
    if isinstance(asn, int):
        return asn
    s = str(asn).strip().upper().replace("AS", "")
    try:
        return int(s)
    except ValueError:
        return None

async def get_as_rank(asn):
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'"}

    url = f"https://api.asrank.caida.org/v2/restful/asns/{asn_int}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                data = await resp.json()
    except Exception as exc:
        return {"asn": f"AS{asn_int}", "error": str(exc)}

    node = data.get("data", {}).get("asn")
    if not node:
        return {"asn": f"AS{asn_int}", "error": "ASN not found in CAIDA AS Rank"}

    degree = node.get("asnDegree", {}) or {}
    cone = node.get("cone", {}) or {}

    customers = degree.get("customer", 0)
    providers = degree.get("provider", 0)
    peers = degree.get("peer", 0)

    if providers == 0:
        relationship_class = "transit-free (Tier 1 or top-level)"
    elif customers == 0:
        relationship_class = "edge (stub network, no customers)"
    else:
        relationship_class = "middle (has both providers and customers)"

    return {
        "asn": f"AS{asn_int}",
        "name": node.get("asnName", ""),
        "global_rank": node.get("rank"),
        "country": (node.get("country") or {}).get("iso", ""),
        "clique_member": node.get("cliqueMember", False),
        "relationship_class": relationship_class,
        "providers": providers,
        "peers": peers,
        "customers": customers,
        "total_neighbors": degree.get("total", 0),
        "customer_cone": {
            "asns": cone.get("numberAsns", 0),
            "prefixes": cone.get("numberPrefixes", 0),
            "addresses": cone.get("numberAddresses", 0),
        },
        "caida_url": f"https://asrank.caida.org/asns/{asn_int}",
        "data_source": "CAIDA AS Rank",
    }


def _relationship_query(asn_int, page_size, offset):
    return (
        '{ asn(asn:"%s") { asn asnName '
        'asnLinks(first:%d, offset:%d) { '
        'totalCount edges { node { relationship asn0 { asn } asn1 { asn asnName } } } '
        '} } }'
    ) % (asn_int, page_size, offset)


async def _fetch_relationship_page(session, asn_int, page_size, offset):
    async with session.post(
        CAIDA_GRAPHQL_URL,
        json={"query": _relationship_query(asn_int, page_size, offset)},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def get_as_relationships(asn, max_links=400):
    """
    Get the actual list of customer, provider, and peer ASNs for a given ASN
    (not just counts). Uses CAIDA's GraphQL asnLinks field.

    We fetch the first page to learn totalCount, then fire every remaining
    page concurrently (bounded by max_links) instead of paginating one page
    at a time — for any operator with >100 relationships (i.e. most real
    operators) this turns what used to be 2-4 sequential round trips into
    effectively one round trip's worth of wall-clock time.
    """
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'"}

    page_size = 100
    customers, providers, peers = [], [], []
    total_count = 0

    try:
        async with aiohttp.ClientSession() as session:
            first_page = await _fetch_relationship_page(session, asn_int, page_size, 0)
            asn_node = first_page.get("data", {}).get("asn")
            if not asn_node:
                return {"asn": f"AS{asn_int}", "error": "ASN not found in CAIDA AS Rank"}

            pages = [asn_node.get("asnLinks", {})]
            total_count = pages[0].get("totalCount", 0)

            remaining_offsets = list(range(page_size, min(total_count, max_links), page_size))
            if remaining_offsets:
                more = await asyncio.gather(
                    *[_fetch_relationship_page(session, asn_int, page_size, off) for off in remaining_offsets],
                    return_exceptions=True,
                )
                for page_result in more:
                    if isinstance(page_result, Exception):
                        continue
                    node = page_result.get("data", {}).get("asn")
                    if node:
                        pages.append(node.get("asnLinks", {}))

        for links in pages:
            for edge in links.get("edges", []):
                node = edge.get("node", {})
                rel = node.get("relationship", "")
                neighbor = node.get("asn1", {})
                entry = {"asn": f"AS{neighbor.get('asn', '')}", "name": neighbor.get("asnName", "")}
                if rel == "customer":
                    customers.append(entry)
                elif rel == "provider":
                    providers.append(entry)
                elif rel == "peer":
                    peers.append(entry)

    except Exception as exc:
        return {"asn": f"AS{asn_int}", "error": str(exc)}

    truncated = total_count is not None and (len(customers) + len(providers) + len(peers)) < total_count

    return {
        "asn": f"AS{asn_int}",
        "total_relationships": total_count,
        "customer_count": len(customers),
        "customers": customers,
        "provider_count": len(providers),
        "providers": providers,
        "peer_count": len(peers),
        "peers": peers[:50],
        "peers_truncated_note": f"Showing first 50 of {len(peers)} peers fetched." if len(peers) > 50 else None,
        "fetch_truncated": truncated,
        "fetch_truncated_note": (
            f"Fetched {max_links} of {total_count} total relationship links. "
            "Increase max_links for a complete list." if truncated else None
        ),
        "caida_url": f"https://asrank.caida.org/asns/{asn_int}",
        "data_source": "CAIDA AS Rank (GraphQL)",
    }


def _country_scan_query(first, offset):
    return (
        '{ asns(first:%d, offset:%d) { totalCount edges { node { '
        'asn asnName rank country { iso } '
        'asnDegree { customer provider peer total } '
        'cone { numberAsns numberPrefixes numberAddresses } '
        '} } } }'
    ) % (first, offset)


async def _fetch_asns_page(session, first, offset):
    async with session.post(
        CAIDA_GRAPHQL_URL,
        json={"query": _country_scan_query(first, offset)},
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        resp.raise_for_status()
        return await resp.json()


async def get_top_asns_by_country(country_iso, top_n=10, max_scan=20000, page_size=1000, batch_size=5):
    """
    Find the top N ASNs registered in a country, ranked by CAIDA's global AS
    Rank (topological significance: customer cone size and transit degree).

    CAIDA's bulk `asns` query has NO country filter argument (confirmed live
    against the schema — it errors with "Unknown argument \"country\" on
    field \"asns\""). But the unfiltered query IS sorted by rank ascending by
    default (confirmed live: first 5 results were LEVEL3, TWELVE99,
    GTT-BACKBONE, COGENT-174, NTT-DATA-2914 — ranks 1-5, all real Tier-1
    backbones). So we paginate the global rank-ordered list in concurrent
    batches and keep whatever matches the target country, stopping once we
    have top_n matches or hit max_scan.

    This is a genuine improvement over sampling an arbitrary-order ASN list
    (RIPEstat's country-asns endpoint, which is not sorted by size at all):
    it's deterministic and surfaces truly globally-significant operators
    first, in a single connector, without an extra per-ASN RIPE lookup.

    CAVEAT: this ranks by CAIDA's topological significance, not by raw
    announced IPv4 prefix count (the metric gtiti_country_operators used to
    report via RIPEstat) — a content network with many small disaggregated
    prefixes might rank differently. For telecom/carrier intelligence,
    topological significance is arguably the more meaningful signal, but it
    is a different metric, worth knowing if comparing against older results.
    """
    country_iso = country_iso.upper()
    matches = []
    scanned = 0
    total_asns_in_dataset = None

    try:
        async with aiohttp.ClientSession() as session:
            offset = 0
            while scanned < max_scan and len(matches) < top_n:
                batch_offsets = [offset + i * page_size for i in range(batch_size)]
                pages = await asyncio.gather(
                    *[_fetch_asns_page(session, page_size, off) for off in batch_offsets],
                    return_exceptions=True,
                )
                any_data = False
                for page in pages:
                    if isinstance(page, Exception):
                        continue
                    asns_node = page.get("data", {}).get("asns")
                    if not asns_node:
                        continue
                    any_data = True
                    if total_asns_in_dataset is None:
                        total_asns_in_dataset = asns_node.get("totalCount")
                    for edge in asns_node.get("edges", []):
                        n = edge.get("node", {})
                        if (n.get("country") or {}).get("iso", "").upper() == country_iso:
                            degree = n.get("asnDegree") or {}
                            cone = n.get("cone") or {}
                            matches.append({
                                "asn": f"AS{n.get('asn')}",
                                "asn_int": int(n.get("asn")),
                                "name": n.get("asnName", ""),
                                "global_rank": n.get("rank"),
                                "customer_cone_asns": cone.get("numberAsns", 0),
                                "customer_cone_prefixes": cone.get("numberPrefixes", 0),
                                "transit_degree": degree.get("total", 0),
                            })
                scanned += page_size * batch_size
                offset += page_size * batch_size
                if not any_data:
                    break
    except Exception as exc:
        return {"error": str(exc), "matches": [], "scanned_globally_ranked_asns": scanned}

    matches.sort(key=lambda m: m["global_rank"])
    return {
        "country": country_iso,
        "matches": matches[:top_n],
        "scanned_globally_ranked_asns": scanned,
        "total_asns_in_dataset": total_asns_in_dataset,
        "truncated_scan": scanned >= max_scan and len(matches) < top_n,
        "data_source": "CAIDA AS Rank (GraphQL, globally rank-ordered scan)",
    }
