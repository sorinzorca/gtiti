import asyncio
import re
import aiohttp

TELEGEOGRAPHY_BASE = "https://www.submarinecablemap.com/api/v3"
_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_cable_cache = None

async def _get_all_cables():
    global _cable_cache
    if _cable_cache is not None:
        return _cable_cache
    headers = {"User-Agent": _BROWSER_UA}
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{TELEGEOGRAPHY_BASE}/cable/all.json", headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            data = await r.json()
    _cable_cache = data.get("features", []) if isinstance(data, dict) else data
    return _cable_cache

async def _get_cable_details(cable_id):
    headers = {"User-Agent": _BROWSER_UA}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{TELEGEOGRAPHY_BASE}/cable/{cable_id}/details.json", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 404:
                    return {}
                r.raise_for_status()
                return await r.json()
    except Exception:
        return {}

def _norm(name):
    return re.sub(r"[^a-z0-9 ]", "", name.lower()).strip()

def _operator_matches(operator_name, owners):
    needle = _norm(operator_name)
    needle_words = set(needle.split())
    for owner in owners:
        owner_norm = _norm(str(owner))
        if needle in owner_norm or owner_norm in needle:
            return True
        if len(needle_words) <= 2 and needle_words.issubset(set(owner_norm.split())):
            return True
    return False

async def get_operator_cables(operator_name):
    try:
        all_cables = await _get_all_cables()
    except Exception as exc:
        return {"operator": operator_name, "cable_memberships": [], "total_cables": 0, "error": f"Failed to fetch cable list: {exc}"}
    matching = []
    for feature in all_cables:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        owners = props.get("owners", [])
        if owners and _operator_matches(operator_name, owners):
            matching.append({"cable_id": props.get("id",""), "cable_name": props.get("name",""), "owners": owners})
    if not matching:
        return {"operator": operator_name, "cable_memberships": [], "total_cables": 0, "note": f"No cable memberships found for '{operator_name}'. Try a shorter name.", "data_source": "TeleGeography Submarine Cable Map"}
    detail_results = await asyncio.gather(*[_get_cable_details(c["cable_id"]) for c in matching], return_exceptions=True)
    memberships = []
    for cable_info, detail in zip(matching, detail_results):
        if isinstance(detail, Exception):
            detail = {}
        memberships.append({"cable_name": cable_info["cable_name"], "cable_id": cable_info["cable_id"], "rfs_date": detail.get("rfs",""), "cable_length_km": detail.get("cable_length",""), "design_capacity_tbps": detail.get("capacity",""), "landing_countries": detail.get("landing_countries",[]), "telegeography_url": f"https://www.submarinecablemap.com/#/submarine-cable/{cable_info['cable_id']}"})
    return {"operator": operator_name, "cable_memberships": memberships, "total_cables": len(memberships), "data_source": "TeleGeography Submarine Cable Map"}

async def get_cables_by_country(country_name):
    try:
        all_cables = await _get_all_cables()
    except Exception as exc:
        return {"country": country_name, "cables": [], "total_cables": 0, "error": str(exc)}
    country_norm = _norm(country_name)
    matching = []
    for feature in all_cables:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        landing = props.get("landing_countries", [])
        if any(country_norm in _norm(c) for c in landing):
            matching.append({"cable_name": props.get("name",""), "cable_id": props.get("id",""), "rfs_date": props.get("rfs",""), "owners": props.get("owners",[]), "telegeography_url": f"https://www.submarinecablemap.com/#/submarine-cable/{props.get('id','')}"})
    return {"country": country_name, "cables": matching, "total_cables": len(matching), "data_source": "TeleGeography Submarine Cable Map"}
