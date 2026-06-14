import os
import csv
import io
import time
import asyncio
import aiohttp

BGP_TOOLS_CONTACT = os.getenv("BGP_TOOLS_CONTACT", "")
_USER_AGENT = f"gtiti-mcp ({BGP_TOOLS_CONTACT or 'no-contact-set, see bgp.tools/kb/api'})"

_class_cache = None
_class_cache_time = 0
_CLASS_TTL = 24 * 3600

def _parse_asn(asn):
    if isinstance(asn, int):
        return asn
    s = str(asn).strip().upper().replace("AS", "")
    try:
        return int(s)
    except ValueError:
        return None

async def _whois_lookup(asn_int):
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection("bgp.tools", 43), timeout=8)
    except Exception as exc:
        return {"error": f"whois connection failed: {exc}"}
    try:
        writer.write(f" -v AS{asn_int}\r\n".encode())
        await writer.drain()
        chunks = []
        while True:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=8)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception as exc:
        return {"error": f"whois read failed: {exc}"}
    finally:
        writer.close()
    full_text = b"".join(chunks).decode(errors="ignore")
    lines = [l.strip() for l in full_text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        return {}
    data_line = lines[-1]
    parts = [p.strip() for p in data_line.split("|")]
    if len(parts) < 7:
        return {"raw": data_line}
    return {"asn": parts[0], "country": parts[3], "registry": parts[4], "allocated_date": parts[5], "whois_name": parts[6]}

async def _get_asn_classifications():
    global _class_cache, _class_cache_time
    now = time.time()
    if _class_cache is not None and (now - _class_cache_time) < _CLASS_TTL:
        return _class_cache
    headers = {"User-Agent": _USER_AGENT}
    async with aiohttp.ClientSession() as session:
        async with session.get("https://bgp.tools/asns.csv", headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            text = await resp.text()
    result = {}
    reader = csv.reader(io.StringIO(text))
    next(reader, None)
    for row in reader:
        if len(row) != 3:
            continue
        asn_str, name, cls = row
        asn_num = _parse_asn(asn_str)
        if asn_num is None:
            continue
        result[asn_num] = {"name": name, "class": cls}
    _class_cache = result
    _class_cache_time = now
    return result

async def get_asn_classification(asn):
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'. Use a number or 'AS1257' format."}
    classifications_task = _get_asn_classifications()
    whois_task = _whois_lookup(asn_int)
    classifications, whois_data = await asyncio.gather(classifications_task, whois_task, return_exceptions=True)
    if isinstance(classifications, Exception):
        classifications = {}
    if isinstance(whois_data, Exception):
        whois_data = {}
    class_info = classifications.get(asn_int, {})
    return {
        "asn": f"AS{asn_int}",
        "name": class_info.get("name") or whois_data.get("whois_name", ""),
        "network_class": class_info.get("class", "Unknown"),
        "country": whois_data.get("country", ""),
        "registry": whois_data.get("registry", ""),
        "allocated_date": whois_data.get("allocated_date", ""),
        "bgp_tools_url": f"https://bgp.tools/as/{asn_int}",
        "data_source": "bgp.tools",
    }
