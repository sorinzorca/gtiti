import aiohttp
from datetime import datetime, timedelta, timezone

RIPESTAT_BASE = "https://stat.ripe.net/data"

def _parse_asn(asn):
    if isinstance(asn, int):
        return asn
    s = str(asn).strip().upper().replace("AS", "")
    try:
        return int(s)
    except ValueError:
        return None

def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None

async def get_routing_history(asn, months_back=12):
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'"}

    starttime = (datetime.now(timezone.utc) - timedelta(days=months_back * 30)).strftime("%Y-%m-%dT%H:%M")
    params = {"resource": f"AS{asn_int}", "starttime": starttime, "min_peers": 5}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{RIPESTAT_BASE}/routing-history/data.json", params=params, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                resp.raise_for_status()
                payload = await resp.json()
    except Exception as exc:
        return {"asn": f"AS{asn_int}", "error": str(exc)}

    data = payload.get("data", {})
    by_origin = data.get("by_origin", [])
    query_endtime = data.get("query_endtime", "")
    query_end_dt = _parse_ts(query_endtime)

    total_prefixes = 0
    withdrawn_prefixes = []
    active_prefixes = 0
    other_origins = set()

    for origin_entry in by_origin:
        origin = str(origin_entry.get("origin", ""))
        if origin != str(asn_int):
            other_origins.add(origin)
            continue
        for prefix_entry in origin_entry.get("prefixes", []):
            prefix = prefix_entry.get("prefix", "")
            total_prefixes += 1
            timelines = prefix_entry.get("timelines", [])
            if not timelines:
                continue
            last_timeline = timelines[-1]
            last_end = last_timeline.get("endtime", "")
            last_end_dt = _parse_ts(last_end)
            if last_end_dt and query_end_dt and (query_end_dt - last_end_dt) > timedelta(days=1):
                withdrawn_prefixes.append({
                    "prefix": prefix,
                    "withdrawn_at": last_end,
                    "peers_seeing_before_withdrawal": last_timeline.get("full_peers_seeing", 0),
                })
            else:
                active_prefixes += 1

    return {
        "asn": f"AS{asn_int}",
        "period_start": data.get("query_starttime", starttime),
        "period_end": query_endtime,
        "total_prefixes_seen": total_prefixes,
        "currently_active_prefixes": active_prefixes,
        "withdrawn_prefixes": withdrawn_prefixes[:10],
        "withdrawn_count": len(withdrawn_prefixes),
        "other_origins_observed": sorted(other_origins),
        "multi_origin_warning": (
            "Prefixes also seen announced by AS" + ", AS".join(sorted(other_origins)) +
            " during this period - possible hijack, reallocation, or anycast/multi-homing."
            if other_origins else None
        ),
        "ripestat_url": f"https://stat.ripe.net/AS{asn_int}#tabId=routing",
        "data_source": "RIPEstat Routing History",
    }
