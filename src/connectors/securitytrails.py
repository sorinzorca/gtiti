import os
import re
import aiohttp
from urllib.parse import urlparse

ST_API_KEY = os.getenv("SECURITYTRAILS_API_KEY", "")
ST_BASE = "https://api.securitytrails.com/v1"

def _extract_domain(website_or_domain):
    if not website_or_domain:
        return None
    s = website_or_domain.strip()
    if "://" in s:
        parsed = urlparse(s)
        host = parsed.netloc
    else:
        host = s
    host = host.split(":")[0]
    host = re.sub(r"^www\.", "", host)
    return host if "." in host else None

async def _st_get(session, path):
    headers = {"APIKEY": ST_API_KEY}
    async with session.get(f"{ST_BASE}{path}", headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as resp:
        if resp.status == 404:
            return None
        resp.raise_for_status()
        return await resp.json()

async def get_domain_intelligence(website_or_domain):
    domain = _extract_domain(website_or_domain)
    if not domain:
        return {"error": f"Could not extract a domain from '{website_or_domain}'. Provide a domain like 'tele2.com' or a full URL."}

    if not ST_API_KEY:
        return {
            "domain": domain,
            "available": False,
            "message": (
                "SECURITYTRAILS_API_KEY not set. Cheapest plan is $500+/mo with no clear free "
                "self-service tier - skipped by choice. IXPDB and Shodan InternetDB cover related "
                "infrastructure signal without cost."
            ),
        }

    try:
        async with aiohttp.ClientSession() as session:
            subdomains_data = await _st_get(session, f"/domain/{domain}/subdomains")
            dns_history_data = await _st_get(session, f"/history/{domain}/dns/a")
            domain_info_data = await _st_get(session, f"/domain/{domain}")
    except Exception as exc:
        return {"domain": domain, "available": False, "error": str(exc)}

    subdomains = (subdomains_data or {}).get("subdomains", [])
    full_subdomains = [f"{s}.{domain}" for s in subdomains][:30]

    dns_records = []
    for record in (dns_history_data or {}).get("records", [])[:10]:
        dns_records.append({
            "first_seen": record.get("first_seen", ""),
            "last_seen": record.get("last_seen", ""),
            "ip_values": [v.get("ip") for v in record.get("values", []) if v.get("ip")],
        })

    current_dns = (domain_info_data or {}).get("current_dns", {})
    a_records = [v.get("ip") for v in current_dns.get("a", {}).get("values", []) if v.get("ip")] if current_dns.get("a") else []

    return {
        "domain": domain,
        "available": True,
        "subdomain_count": len(subdomains),
        "subdomains": full_subdomains,
        "current_a_records": a_records,
        "dns_history_a_records": dns_records,
        "data_source": "SecurityTrails",
        "securitytrails_url": f"https://securitytrails.com/domain/{domain}/dns",
    }
