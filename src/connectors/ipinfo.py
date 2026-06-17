import os
import asyncio
import aiohttp

IPINFO_TOKEN = os.getenv("IPINFO_TOKEN", "")
IPINFO_BASE = "https://api.ipinfo.io/lite"

async def _lookup_single_ip(session, ip):
    try:
        async with session.get(f"{IPINFO_BASE}/{ip}", params={"token": IPINFO_TOKEN}, timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 401:
                return {"ip": ip, "error": "Invalid or missing IPINFO_TOKEN"}
            resp.raise_for_status()
            return await resp.json()
    except Exception as exc:
        return {"ip": ip, "error": str(exc)}

async def verify_prefix_ownership(prefixes, expected_org_keyword=None):
    if not IPINFO_TOKEN:
        return {
            "available": False,
            "message": (
                "IPINFO_TOKEN not set. Add it to .env to enable IP-to-ASN/org verification. "
                "Free unlimited Lite tier, no credit card: sign up at ipinfo.io to get a token."
            ),
        }

    if not prefixes:
        return {"available": True, "checked": [], "note": "No prefixes provided to verify."}

    import ipaddress
    sample_ips = []
    for prefix in prefixes[:6]:
        try:
            network = ipaddress.ip_network(prefix, strict=False)
            sample_ip = str(network.network_address + 1) if network.num_addresses > 1 else str(network.network_address)
            sample_ips.append(sample_ip)
        except ValueError:
            continue

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[_lookup_single_ip(session, ip) for ip in sample_ips])

    verified = []
    mismatches = []
    for r in results:
        if "error" in r:
            continue
        entry = {
            "ip": r.get("ip"),
            "asn": r.get("asn"),
            "as_name": r.get("as_name"),
            "as_domain": r.get("as_domain"),
            "country": r.get("country"),
        }
        verified.append(entry)
        if expected_org_keyword:
            as_name_lower = (r.get("as_name") or "").lower()
            if expected_org_keyword.lower() not in as_name_lower:
                mismatches.append(entry)

    return {
        "available": True,
        "checked_count": len(verified),
        "results": verified,
        "mismatches": mismatches if expected_org_keyword else None,
        "mismatch_note": (
            f"{len(mismatches)} of {len(verified)} sampled IPs resolved to an org name "
            f"NOT containing '{expected_org_keyword}' - worth double-checking prefix ownership."
            if expected_org_keyword and mismatches else None
        ),
        "data_source": "IPinfo Lite (free tier)",
    }
