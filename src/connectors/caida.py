import aiohttp

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
