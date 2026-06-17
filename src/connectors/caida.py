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


async def get_as_relationships(asn, max_links=400):
    """
    Get the actual list of customer, provider, and peer ASNs for a given ASN
    (not just counts). Uses CAIDA's GraphQL asnLinks field, paginating until
    all links are fetched or max_links is reached.
    """
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'"}

    customers = []
    providers = []
    peers = []
    offset = 0
    page_size = 100
    total_count = None

    try:
        async with aiohttp.ClientSession() as session:
            while True:
                query = (
                    '{ asn(asn:"%s") { asn asnName '
                    'asnLinks(first:%d, offset:%d) { '
                    'totalCount edges { node { relationship asn0 { asn } asn1 { asn asnName } } } '
                    '} } }'
                ) % (asn_int, page_size, offset)

                async with session.post(
                    CAIDA_GRAPHQL_URL,
                    json={"query": query},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    resp.raise_for_status()
                    data = await resp.json()

                asn_node = data.get("data", {}).get("asn")
                if not asn_node:
                    return {"asn": f"AS{asn_int}", "error": "ASN not found in CAIDA AS Rank"}

                links = asn_node.get("asnLinks", {})
                if total_count is None:
                    total_count = links.get("totalCount", 0)

                edges = links.get("edges", [])
                if not edges:
                    break

                for edge in edges:
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

                offset += page_size
                if offset >= total_count or offset >= max_links:
                    break

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
