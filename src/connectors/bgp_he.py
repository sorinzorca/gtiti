"""
Hurricane Electric BGP Toolkit connector (bgp.he.net).

Complements bgp_tools.get_asn_classification (network-type classification
via bgp.tools + whois) with HE's named peer/upstream view and RPKI/prefix
summary stats, which bgp.tools doesn't expose.

bgp.he.net has no official public API, so this parses the public AS info
page (e.g. https://bgp.he.net/AS15169). Two known limitations:

  1. HE has no documented rate limit but will block IPs that hammer it —
     this is a lookup tool, not a bulk crawler. Keep call volume low.
  2. The "Prefixes v4/v6" tab is loaded client-side via JS/AJAX and is NOT
     present in the static HTML this fetches. Peers v4/v6 and the summary
     stats panel (RPKI, prefix counts, peer counts, country, IX count) ARE
     in the static HTML and are what this returns. For the actual prefix
     list, RIPEstat's announced-prefixes API is a stable alternative:
     https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS15169
"""

import os
import re
import aiohttp

BGP_HE_BASE = "https://bgp.he.net"
_CONTACT = os.getenv("BGP_TOOLS_CONTACT", "")
_USER_AGENT = f"gtiti-mcp ({_CONTACT or 'no-contact-set'})"

_SUMMARY_PATTERNS = {
    "internet_exchanges": r"Internet Exchanges:\s*([\d,]+)",
    "prefixes_originated_all": r"Prefixes Originated \(all\):\s*([\d,]+)",
    "prefixes_originated_v4": r"Prefixes Originated \(v4\):\s*([\d,]+)",
    "prefixes_originated_v6": r"Prefixes Originated \(v6\):\s*([\d,]+)",
    "prefixes_announced_all": r"Prefixes Announced \(all\):\s*([\d,]+)",
    "prefixes_announced_v4": r"Prefixes Announced \(v4\):\s*([\d,]+)",
    "prefixes_announced_v6": r"Prefixes Announced \(v6\):\s*([\d,]+)",
    "rpki_valid_all": r"RPKI Originated Valid \(all\):\s*([\d,]+)",
    "rpki_invalid_all": r"RPKI Originated Invalid \(all\):\s*([\d,]+)",
    "bgp_peers_observed_all": r"BGP Peers Observed \(all\):\s*([\d,]+)",
    "bgp_peers_observed_v4": r"BGP Peers Observed \(v4\):\s*([\d,]+)",
    "bgp_peers_observed_v6": r"BGP Peers Observed \(v6\):\s*([\d,]+)",
    "avg_as_path_length": r"Average AS Path Length \(all\):\s*([\d.]+)",
}

_PEER_ROW_RE = re.compile(
    r'href="/(AS\d+)"[^>]*>\s*(AS\d+)\s*</a>.*?href="/\1"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)


def _parse_asn(asn):
    s = str(asn).strip().upper().replace("AS", "")
    try:
        return int(s)
    except ValueError:
        return None


def _strip_tags(html_fragment):
    return re.sub(r"<[^>]+>", "", html_fragment).strip()


def _parse_summary(html):
    stats = {}
    for key, pattern in _SUMMARY_PATTERNS.items():
        m = re.search(pattern, html)
        if m:
            stats[key] = m.group(1).replace(",", "")

    country_match = re.search(
        r'href="/country/[A-Z]{2}"[^>]*>\s*([^<]+?)\s*<', html
    )
    if country_match:
        stats["country_of_origin"] = country_match.group(1).strip()

    website_match = re.search(
        r"Company Website:.*?<a[^>]*href=\"([^\"]+)\"", html, re.DOTALL
    )
    if website_match:
        stats["company_website"] = website_match.group(1).strip()

    return stats


def _parse_peer_section(html, heading_text):
    """
    Find the block of HTML following a heading like 'AS15169 IPv4 Peers'
    up to the next <h2>/<h3>, then pull out (asn, name) pairs from the
    ASN links inside it. Regex, not an HTML parser — matches this
    project's existing style (see bgp_tools.py) and avoids adding a new
    dependency (beautifulsoup4) for a single connector.
    """
    heading_match = re.search(re.escape(heading_text), html, re.IGNORECASE)
    if not heading_match:
        return []
    section_start = heading_match.end()
    next_heading = re.search(r"<h[1-4][^>]*>", html[section_start:])
    section_end = (
        section_start + next_heading.start() if next_heading else len(html)
    )
    section_html = html[section_start:section_end]

    peers = []
    seen = set()
    for m in re.finditer(r'href="/AS(\d+)"[^>]*>\s*([^<]*?)\s*</a>', section_html):
        asn_num, label = m.group(1), m.group(2).strip()
        asn_id = f"AS{asn_num}"
        # Labels alternate between the ASN itself (e.g. "AS6453") and the
        # network name (e.g. "TATA COMMUNICATIONS..."); only keep the ones
        # that read as a name. Dedup on asn_id only AFTER we've accepted a
        # name match, so we don't let the (skipped) ASN-label match consume
        # the slot before the real name match arrives.
        if asn_id in seen:
            continue
        if label and not label.upper().startswith("AS"):
            peers.append({"asn": asn_id, "name": label})
            seen.add(asn_id)
    return peers


async def get_bgp_he_lookup(asn):
    """
    Look up an ASN on Hurricane Electric's BGP Toolkit (bgp.he.net).

    Returns named IPv4/IPv6 peers, RPKI/prefix/peer summary counts,
    country of origin, and company website — a complement to
    get_asn_classification (bgp.tools-based network-type classification).
    """
    asn_int = _parse_asn(asn)
    if asn_int is None:
        return {"error": f"Invalid ASN: '{asn}'. Use a number or 'AS1257' format."}

    asn_id = f"AS{asn_int}"
    url = f"{BGP_HE_BASE}/{asn_id}"
    headers = {"User-Agent": _USER_AGENT}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return {
                        "error": f"bgp.he.net returned HTTP {resp.status} for {asn_id}",
                        "source_url": url,
                    }
                html = await resp.text()
    except Exception as exc:
        return {"error": f"Request to bgp.he.net failed: {exc}", "source_url": url}

    title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    name = (
        _strip_tags(title_match.group(1)).replace(" - bgp.he.net", "").strip()
        if title_match
        else asn_id
    )

    return {
        "asn": asn_id,
        "name": name,
        "source_url": url,
        "summary": _parse_summary(html),
        "ipv4_peers": _parse_peer_section(html, f"{asn_id} IPv4 Peers"),
        "ipv6_peers": _parse_peer_section(html, f"{asn_id} IPv6 Peers"),
        "data_source": "bgp.he.net",
        "note": (
            "Prefix lists are loaded client-side on bgp.he.net and are not "
            "included here. For a stable, API-based prefix list use RIPEstat: "
            f"https://stat.ripe.net/data/announced-prefixes/data.json?resource={asn_id}"
        ),
    }
