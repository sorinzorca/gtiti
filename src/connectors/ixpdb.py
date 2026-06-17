import aiohttp

IXPDB_BASE = "https://api.ixpdb.net"

def _parse_asn(asn):
    if isinstance(asn, int):
        return asn
    s = str(asn).strip().upper().replace("AS", "")
    try:
        return int(s)
    except ValueError:
        return None

async def get_ixpdb_presence(asn):
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{IXPDB_BASE}/v1/participant/{asn_int}", timeout=aiohttp.ClientTimeout(total=12)) as resp:
                if resp.status == 404:
                    return {
                        "asn": f"AS{asn_int}",
                        "found": False,
                        "message": "ASN not found in IXPDB. It may not participate in any IX-F-reporting IXP, or the IXP hasn't published a member export.",
                    }
                resp.raise_for_status()
                participant = await resp.json()

            async with session.get(f"{IXPDB_BASE}/v1/participant/{asn_int}/providers", timeout=aiohttp.ClientTimeout(total=12)) as resp2:
                resp2.raise_for_status()
                providers = await resp2.json()
    except Exception as exc:
        return {"asn": f"AS{asn_int}", "error": str(exc)}

    ixp_list = [
        {"ixpdb_id": p.get("id"), "name": p.get("name", ""), "city": p.get("city", ""), "country": p.get("country", "")}
        for p in providers
    ] if isinstance(providers, list) else []

    return {
        "asn": f"AS{asn_int}",
        "found": True,
        "name": participant.get("name", ""),
        "organization_id": participant.get("organization_id"),
        "peering_ip_count": len(participant.get("ip_addresses", [])),
        "ixp_count": len(ixp_list),
        "ixp_presence": ixp_list,
        "data_source": "IXPDB (Euro-IX) - independent of PeeringDB, sourced from IX-F member exports",
        "note": "This is a separate data source from PeeringDB and may show IXPs not visible in gtiti_operator_lookup, or vice versa.",
    }
