import ipaddress
import asyncio
import aiohttp

INTERNETDB_BASE = "https://internetdb.shodan.io"

async def _lookup_ip(session, ip_str):
    try:
        async with session.get(f"{INTERNETDB_BASE}/{ip_str}", timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status == 404:
                return {"ip": ip_str, "ports": [], "vulns": [], "tags": [], "hostnames": [], "note": "No data in InternetDB for this IP."}
            resp.raise_for_status()
            return await resp.json()
    except Exception as exc:
        return {"ip": ip_str, "error": str(exc)}

def _sample_ip_from_prefix(prefix_str):
    try:
        network = ipaddress.ip_network(prefix_str, strict=False)
        if network.num_addresses < 2:
            return str(network.network_address)
        return str(network.network_address + 1)
    except ValueError:
        return None

async def check_exposure(prefixes):
    if not prefixes:
        return {
            "checked_ips": [],
            "results": [],
            "note": "No prefixes provided to check. This is a spot-check, not a full network sweep.",
        }

    sample_ips = []
    for prefix in prefixes[:8]:
        ip = _sample_ip_from_prefix(prefix)
        if ip:
            sample_ips.append(ip)

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[_lookup_ip(session, ip) for ip in sample_ips])

    risky_ports = {21, 22, 23, 445, 3389, 5900}
    flagged = []
    total_vulns = 0
    for r in results:
        if "error" in r:
            continue
        ports = r.get("ports", [])
        vulns = r.get("vulns", [])
        total_vulns += len(vulns)
        exposed_risky = sorted(set(ports) & risky_ports)
        if exposed_risky or vulns:
            flagged.append({
                "ip": r.get("ip"),
                "exposed_risky_ports": exposed_risky,
                "all_open_ports": ports,
                "cve_count": len(vulns),
                "cves": vulns[:5],
                "tags": r.get("tags", []),
                "hostnames": r.get("hostnames", []),
            })

    return {
        "checked_ips": sample_ips,
        "ips_checked_count": len(sample_ips),
        "ips_with_findings": len(flagged),
        "total_cves_found": total_vulns,
        "flagged_hosts": flagged,
        "scope_note": (
            "This checks the first usable address of up to 8 sample prefixes "
            "(common router/gateway position) - it is a spot-check, not a full "
            "network sweep. Absence of findings does not mean the network is clean."
        ),
        "data_source": "Shodan InternetDB (free, no key, updated weekly)",
    }
