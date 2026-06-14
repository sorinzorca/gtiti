import os
import aiohttp

CF_API_TOKEN = os.getenv("CF_API_TOKEN", "")
CF_BASE = "https://api.cloudflare.com/client/v4"

def _parse_asn(asn):
    if isinstance(asn, int):
        return asn
    s = str(asn).strip().upper().replace("AS", "")
    try:
        return int(s)
    except ValueError:
        return None

async def _cf_get(path, params=None):
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{CF_BASE}{path}", headers=headers, params=params or {}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.json()

async def get_radar_profile(asn):
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'"}

    if not CF_API_TOKEN:
        return {
            "asn": f"AS{asn_int}",
            "available": False,
            "message": (
                "CF_API_TOKEN not set. Add it to .env to enable Cloudflare Radar data "
                "(RPKI validation status, AS relationships, traffic confidence). "
                "Free with any Cloudflare account: dash.cloudflare.com -> My Profile -> API Tokens -> "
                "Create Token -> use the 'Read' template or grant Account > Cloudflare Radar > Read."
            ),
        }

    try:
        routing_stats = await _cf_get("/radar/bgp/routes/stats", {"asn": asn_int})
    except Exception as exc:
        routing_stats = {"error": str(exc)}

    try:
        relationships = await _cf_get(f"/radar/entities/asns/{asn_int}/rel")
    except Exception as exc:
        relationships = {"error": str(exc)}

    try:
        asn_info = await _cf_get(f"/radar/entities/asns/{asn_int}")
    except Exception as exc:
        asn_info = {"error": str(exc)}

    stats_result = routing_stats.get("result", {}).get("stats", {}) if isinstance(routing_stats, dict) and "result" in routing_stats else {}
    rpki = {
        "valid": stats_result.get("rpki_valid", {}),
        "invalid": stats_result.get("rpki_invalid", {}),
        "unknown": stats_result.get("rpki_unknown", {}),
        "total_prefixes": stats_result.get("pfxs_count", stats_result.get("prefixes_count")),
    }

    rels_result = relationships.get("result", {}) if isinstance(relationships, dict) and "result" in relationships else {}
    rels_list = rels_result.get("rels", [])
    rel_counts = {}
    for r in rels_list:
        rel_type = r.get("rel", "unknown")
        rel_counts[rel_type] = rel_counts.get(rel_type, 0) + 1

    info_result = asn_info.get("result", {}).get("asn", {}) if isinstance(asn_info, dict) and "result" in asn_info else {}

    return {
        "asn": f"AS{asn_int}",
        "available": True,
        "name": info_result.get("name", ""),
        "org_name": info_result.get("orgName", ""),
        "country": info_result.get("country", ""),
        "estimated_users": info_result.get("estimatedUsers", {}),
        "rpki_validation": rpki,
        "relationship_summary": rel_counts,
        "total_peers_observed": rels_result.get("meta", {}).get("total_peers", len(rels_list)),
        "radar_url": f"https://radar.cloudflare.com/as{asn_int}",
        "data_source": "Cloudflare Radar",
        "raw_routing_stats": routing_stats if "error" in routing_stats else None,
        "raw_relationships_error": relationships.get("error") if "error" in relationships else None,
        "raw_asn_info_error": asn_info.get("error") if "error" in asn_info else None,
    }
