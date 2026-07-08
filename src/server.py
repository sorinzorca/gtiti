"""
GTITI MCP Server — Phase 1 + Phase 2 + Connectors 7-13
"""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

from src.tools.operator import lookup_operator, operators_in_country, lookup_ixp
from src.tools.briefing import build_full_briefing
from src.connectors.news import get_operator_news
from src.connectors.crunchbase import get_company_intelligence
from src.connectors.submarine import get_operator_cables, get_cables_by_country
from src.connectors.contacts import get_wholesale_contacts, get_executive_and_commercial_contacts
from src.connectors.bgp_tools import get_asn_classification
from src.connectors.bgp_he import get_bgp_he_lookup
from src.connectors.caida import get_as_rank, get_as_relationships
from src.connectors.cloudflare_radar import get_radar_profile
from src.connectors.routing_history import get_routing_history
from src.connectors.shodan_exposure import check_exposure
from src.connectors.ixpdb import get_ixpdb_presence
from src.connectors.securitytrails import get_domain_intelligence
from src.connectors.ipinfo import verify_prefix_ownership

app = Server("gtiti")

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="gtiti_operator_lookup",
            description="Look up detailed intelligence on a telecom operator / network / ISP / carrier. Returns: ASN(s), IXP presence, peering policy, peering contacts (NOC, peering team), facility presence, BGP prefix counts, upstream providers, and PeeringDB link. Use this when asked about a specific company: 'tell me about Deutsche Telekom', 'what ASNs does Tele2 have', 'who handles peering at NTT Communications', 'is Vivo present at IX.br São Paulo'.",
            inputSchema={"type": "object", "properties": {"query": {"type": "string", "description": "Operator name, ASN, or both. Examples: 'Deutsche Telekom', 'AS3320', 'Vivo Brazil', 'NTT Communications', 'MTN Nigeria', 'AS10429'"}}, "required": ["query"]},
        ),
        types.Tool(
            name="gtiti_country_operators",
            description="Find the most important telecom operators / ISPs / carriers in a specific country. Returns a ranked list by network size (IPv4 prefix count), with ASNs, peering policy, and PeeringDB presence. Use this when asked: 'who are the main telecoms in Brazil', 'most important ISPs in Germany', 'telecom operators in Nigeria', 'which networks are dominant in Japan'.",
            inputSchema={"type": "object", "properties": {"country_code": {"type": "string", "description": "ISO 2-letter country code. Examples: DE (Germany), BR (Brazil), JP (Japan), NG (Nigeria), IN (India), US (United States), FR (France), GB (United Kingdom), ZA (South Africa)"}, "top_n": {"type": "integer", "description": "How many operators to return. Default is 10, max 20.", "default": 10}}, "required": ["country_code"]},
        ),
        types.Tool(
            name="gtiti_ixp_lookup",
            description="Look up an Internet Exchange Point (IXP) and list its member networks. Returns: IXP location, member count, and list of networks present. Use this when asked: 'who is present at DE-CIX Frankfurt', 'members of LINX', 'which networks peer at AMS-IX', 'who is at IX.br São Paulo'.",
            inputSchema={"type": "object", "properties": {"ixp_name": {"type": "string", "description": "IXP name or partial name. Examples: 'DE-CIX Frankfurt', 'LINX', 'AMS-IX', 'IX.br', 'JPIX', 'HKIX', 'IXPN Lagos'"}}, "required": ["ixp_name"]},
        ),
        types.Tool(
            name="gtiti_operator_news",
            description="Fetch recent news, investments, and M&A announcements for a telecom operator. Use this when asked: 'What has MTN Nigeria announced lately?', 'Any recent investments by NTT?', 'news about Tele2 Sweden'.",
            inputSchema={"type": "object", "properties": {"operator_name": {"type": "string", "description": "Operator name. Examples: 'MTN Nigeria', 'Deutsche Telekom', 'NTT Communications'"}, "months_back": {"type": "integer", "description": "How many months back to search. Default 6.", "default": 6}}, "required": ["operator_name"]},
        ),
        types.Tool(
            name="gtiti_company_intelligence",
            description="Fetch Crunchbase data for a telecom operator: funding rounds, acquisitions, investors, revenue range, employee count, and executive names. Use this when asked: 'What acquisitions has Zayo made?', 'Who invested in Brisanet?', 'financial profile of Cogent'.",
            inputSchema={"type": "object", "properties": {"company_name": {"type": "string", "description": "Company name. Examples: 'Zayo', 'Cogent', 'Brisanet', 'Tele2'"}}, "required": ["company_name"]},
        ),
        types.Tool(
            name="gtiti_submarine_cables",
            description="Find submarine cable memberships for a telecom operator, or list all cables landing in a country. Use this when asked: 'Which submarine cables is Telecom Italia on?', 'What cables land in Nigeria?', 'NTT submarine cable membership'.",
            inputSchema={"type": "object", "properties": {"operator_name": {"type": "string", "description": "Operator name to find cable memberships for. Leave empty if using country_name."}, "country_name": {"type": "string", "description": "Country name to list all cables landing there. Examples: 'Nigeria', 'Japan', 'Brazil'. Leave empty if using operator_name."}}},
        ),
        types.Tool(
            name="gtiti_wholesale_contacts",
            description="Find wholesale, peering, and carrier-relations contacts for a telecom operator beyond what PeeringDB provides. Use this when asked: 'Who is the VP of Wholesale at Telefónica Brasil?', 'LinkedIn contacts at Tele2 Sweden', 'peering team at NTT'.",
            inputSchema={"type": "object", "properties": {"company_name": {"type": "string", "description": "Company name. Examples: 'Tele2 Sweden', 'Telefónica Brasil'"}, "roles": {"type": "array", "items": {"type": "string"}, "description": "Optional list of title keywords. Defaults to: VP Wholesale, Head of Peering, Carrier Relations, Director International."}}, "required": ["company_name"]},
        ),
        types.Tool(
            name="gtiti_executive_contacts",
            description="Find LinkedIn profile links for an operator's key people: CEO, CTO, COO (executive leadership) and VP Wholesale, Head of Peering, Carrier Relations, B2B leadership (commercial/technical decision-makers). Always returns clickable LinkedIn search links or live profile URLs - never imports or reproduces LinkedIn content directly. Use this when asked: 'who is the CEO of Tele2?', 'find the CTO and wholesale contacts for Deutsche Telekom', 'LinkedIn links for NTT's leadership and B2B team'.",
            inputSchema={"type": "object", "properties": {"company_name": {"type": "string", "description": "Company name. Examples: 'Tele2 Sweden', 'Deutsche Telekom'"}, "include_executives": {"type": "boolean", "description": "Include CEO/CTO/COO search. Default true.", "default": True}, "include_b2b": {"type": "boolean", "description": "Include VP Wholesale/Peering/B2B search. Default true.", "default": True}}, "required": ["company_name"]},
        ),
        types.Tool(
            name="gtiti_bgp_classification",
            description="Classify a telecom operator's network type (Eyeball, Transit, Content, Enterprise, or Unknown) using bgp.tools, and cross-check the country, registry, and allocation date for the ASN via whois. Use this when asked: 'what type of network is AS1257?', 'is Cogent a transit network?', 'when was AS3320 allocated?'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '1257', 'AS1257', 'AS3320'"}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_bgp_he_lookup",
            description="Look up an ASN on Hurricane Electric's BGP Toolkit (bgp.he.net). Returns named IPv4/IPv6 peers (who this network actually peers with, by name), plus RPKI/prefix/peer summary counts, country of origin, and company website. Complements gtiti_bgp_classification (bgp.tools-based network-type classification) with HE's named peer view. Note: prefix lists themselves are not included (bgp.he.net loads those client-side) — use gtiti_verify_prefix_ownership or RIPEstat for actual prefixes. Use this when asked: 'who peers with AS15169?', 'show me Cogent's peers on Hurricane Electric', 'HE BGP toolkit view of this ASN'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '15169', 'AS15169', 'AS3320'"}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_as_rank",
            description="Get CAIDA's global AS rank, customer cone size, and provider/peer/customer relationship counts for an ASN. Reveals whether a network is transit-free (Tier 1), an edge network, or a middle network, and how many downstream ASNs, prefixes, and IP addresses it can reach. Use this when asked: 'how significant is AS1257 in the global routing system?', 'what's the customer cone of Cogent?', 'is this operator transit-free?'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '1257', 'AS1257', 'AS3320'"}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_as_relationships",
            description="Get the ACTUAL LIST of customer, provider, and peer ASNs for an operator (not just counts) using CAIDA's relationship data. Reveals exactly which networks buy transit from this operator, which networks this operator buys transit from, and who it peers with. Use this when asked: 'who are Tele2's actual upstream providers?', 'list the ASNs that are customers of AS1257', 'what networks does this operator peer with by name?'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '1257', 'AS1257', 'AS3320'"}, "max_links": {"type": "integer", "description": "Maximum relationship links to fetch (paginated). Default 400, covers most operators fully.", "default": 400}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_cloudflare_radar",
            description="Get Cloudflare Radar's view of an ASN: RPKI validation status (valid/invalid/unknown prefix percentages), AS-level relationships (peers/customers/providers as seen by Cloudflare), estimated user population, and traffic confidence. Requires a free Cloudflare API token. Use this when asked: 'what's the RPKI health of AS1257?', 'does this operator have RPKI invalid routes?', 'how many users does Cloudflare estimate for this ASN?'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '1257', 'AS1257', 'AS3320'"}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_routing_history",
            description="Get BGP routing history for an ASN over a configurable lookback period: total prefixes seen, currently active prefixes, recently withdrawn prefixes (with withdrawal dates), and any other ASNs observed announcing the same prefixes (a signal for hijacks, reallocations, or anycast/multi-homing). Use this when asked: 'has this operator had any BGP incidents recently?', 'what prefixes has AS1257 withdrawn?', 'any routing anomalies for Cogent in the last year?'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '1257', 'AS1257', 'AS3320'"}, "months_back": {"type": "integer", "description": "How many months back to look. Default 12.", "default": 12}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_security_exposure",
            description="Spot-check a telecom operator's network for exposed services, open risky ports (SSH, Telnet, RDP, SMB, VNC), and known CVEs using Shodan InternetDB. Looks up the operator's announced prefixes (via PeeringDB/RIPE) and checks sample gateway IPs from each. Free, no key, updated weekly. This is a spot-check on a handful of IPs, not a full network sweep. Use this when asked: 'does Tele2 have any exposed services?', 'security exposure check for Cogent', 'any known CVEs on this operator's network?'.",
            inputSchema={"type": "object", "properties": {"operator_name": {"type": "string", "description": "Operator name. Examples: 'Tele2 Sweden', 'Deutsche Telekom'"}}, "required": ["operator_name"]},
        ),
        types.Tool(
            name="gtiti_verify_prefix_ownership",
            description="Verify which organization actually owns a telecom operator's announced IP prefixes using IPinfo. Cross-checks PeeringDB/RIPE-reported ownership against an independent IP-to-ASN/org data source, and flags any sample IPs that resolve to an unexpected organization name. Requires a free IPinfo token (unlimited Lite tier, no credit card). Use this when asked: 'verify Tele2 actually owns these IP ranges', 'cross-check prefix ownership for AS1257', 'does this operator's IP space match what they claim?'.",
            inputSchema={"type": "object", "properties": {"operator_name": {"type": "string", "description": "Operator name. Examples: 'Tele2 Sweden', 'Deutsche Telekom'"}, "expected_org_keyword": {"type": "string", "description": "Optional keyword to check for in the resolved org name, e.g. 'Tele2'. If omitted, just reports what each sampled IP resolves to."}}, "required": ["operator_name"]},
        ),
        types.Tool(
            name="gtiti_ixpdb_lookup",
            description="Look up an operator's Internet Exchange Point presence via IXPDB (Euro-IX), an independent data source from PeeringDB sourced directly from IXP IX-F member exports. Often reveals IXP memberships not visible in gtiti_operator_lookup, especially at IXPs that report to Euro-IX but not PeeringDB. Use this when asked: 'cross-check Tele2's IXP presence', 'is this operator at any IXPs not in PeeringDB?', 'IXPDB data for AS1257'.",
            inputSchema={"type": "object", "properties": {"asn": {"type": "string", "description": "ASN as a number or 'AS' prefixed string. Examples: '1257', 'AS1257', 'AS3320'"}}, "required": ["asn"]},
        ),
        types.Tool(
            name="gtiti_domain_intelligence",
            description="Get subdomain mapping and historical DNS records for a telecom operator's domain via SecurityTrails. Reveals the operator's full domain infrastructure footprint (subdomains for NOC, customer portals, peering, internal tools) and DNS history showing past IP assignments. Requires a free SecurityTrails API key (2,500 queries/month). Use this when asked: 'what subdomains does Tele2 have?', 'DNS history for tele2.com', 'map this operator's domain infrastructure'.",
            inputSchema={"type": "object", "properties": {"domain_or_operator": {"type": "string", "description": "A domain name (e.g. 'tele2.com') or operator name to look up via gtiti_operator_lookup first to find their website."}}, "required": ["domain_or_operator"]},
        ),
        types.Tool(
            name="gtiti_full_briefing",
            description="Generate a COMPLETE operator briefing combining ALL data sources: ASNs, IXP presence (PeeringDB + IXPDB cross-check), BGP prefixes, peering contacts, network classification, AS rank and customer cone, RPKI/routing security health, BGP routing history and anomalies, security exposure spot-check, domain/subdomain intelligence, recent news, Crunchbase financials, submarine cable memberships, and wholesale LinkedIn contacts. Use this when asked: 'Prepare a full briefing on Tele2 Sweden', 'I have a meeting with Deutsche Telekom, give me everything', 'complete profile of NTT Communications'. Pass fast=true for a quicker version that skips submarine cables, BGP routing history, the Shodan security spot-check, IXPDB cross-check, and domain intelligence — useful when you just need the core identity/ASN/contacts picture in a hurry (e.g. 'quick briefing on Tele2 before my call in 5 minutes').",
            inputSchema={"type": "object", "properties": {"operator_name": {"type": "string", "description": "Operator name. Examples: 'Tele2 Sweden', 'Deutsche Telekom', 'NTT Communications'"}, "fast": {"type": "boolean", "description": "Skip the slower/less-essential sections (submarine cables, routing history, security exposure spot-check, IXPDB cross-check, domain intelligence) for a much quicker result. Default false.", "default": False}}, "required": ["operator_name"]},
        ),
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "gtiti_operator_lookup":
            query = arguments.get("query", "").strip()
            if not query:
                raise ValueError("'query' parameter is required and cannot be empty.")
            result = await lookup_operator(query)
        elif name == "gtiti_country_operators":
            country_code = arguments.get("country_code", "").strip().upper()
            if not country_code:
                raise ValueError("'country_code' parameter is required.")
            top_n = min(int(arguments.get("top_n", 10)), 20)
            result = await operators_in_country(country_code, top_n)
        elif name == "gtiti_ixp_lookup":
            ixp_name = arguments.get("ixp_name", "").strip()
            if not ixp_name:
                raise ValueError("'ixp_name' parameter is required.")
            result = await lookup_ixp(ixp_name)
        elif name == "gtiti_operator_news":
            operator_name = arguments.get("operator_name", "").strip()
            if not operator_name:
                raise ValueError("'operator_name' parameter is required.")
            result = await get_operator_news(operator_name, months_back=int(arguments.get("months_back", 6)))
        elif name == "gtiti_company_intelligence":
            company_name = arguments.get("company_name", "").strip()
            if not company_name:
                raise ValueError("'company_name' parameter is required.")
            result = await get_company_intelligence(company_name)
        elif name == "gtiti_submarine_cables":
            operator_name = arguments.get("operator_name", "").strip()
            country_name  = arguments.get("country_name", "").strip()
            if country_name and not operator_name:
                result = await get_cables_by_country(country_name)
            elif operator_name:
                result = await get_operator_cables(operator_name)
            else:
                raise ValueError("Provide either 'operator_name' or 'country_name'.")
        elif name == "gtiti_wholesale_contacts":
            company_name = arguments.get("company_name", "").strip()
            if not company_name:
                raise ValueError("'company_name' parameter is required.")
            result = await get_wholesale_contacts(company_name, roles=arguments.get("roles", None))
        elif name == "gtiti_executive_contacts":
            company_name = arguments.get("company_name", "").strip()
            if not company_name:
                raise ValueError("'company_name' parameter is required.")
            include_executives = arguments.get("include_executives", True)
            include_b2b = arguments.get("include_b2b", True)
            result = await get_executive_and_commercial_contacts(company_name, include_executives=include_executives, include_b2b=include_b2b)
        elif name == "gtiti_bgp_classification":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            result = await get_asn_classification(asn)
        elif name == "gtiti_bgp_he_lookup":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            result = await get_bgp_he_lookup(asn)
        elif name == "gtiti_as_rank":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            result = await get_as_rank(asn)
        elif name == "gtiti_as_relationships":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            max_links = int(arguments.get("max_links", 400))
            result = await get_as_relationships(asn, max_links=max_links)
        elif name == "gtiti_cloudflare_radar":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            result = await get_radar_profile(asn)
        elif name == "gtiti_routing_history":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            months_back = int(arguments.get("months_back", 12))
            result = await get_routing_history(asn, months_back=months_back)
        elif name == "gtiti_security_exposure":
            operator_name = arguments.get("operator_name", "").strip()
            if not operator_name:
                raise ValueError("'operator_name' parameter is required.")
            operator_data = await lookup_operator(operator_name)
            prefixes = operator_data.get("sample_prefixes_ipv4", [])
            result = await check_exposure(prefixes)
            result["operator"] = operator_name
        elif name == "gtiti_verify_prefix_ownership":
            operator_name = arguments.get("operator_name", "").strip()
            if not operator_name:
                raise ValueError("'operator_name' parameter is required.")
            expected_org_keyword = arguments.get("expected_org_keyword", None)
            operator_data = await lookup_operator(operator_name)
            prefixes = operator_data.get("sample_prefixes_ipv4", [])
            result = await verify_prefix_ownership(prefixes, expected_org_keyword=expected_org_keyword)
            result["operator"] = operator_name
        elif name == "gtiti_ixpdb_lookup":
            asn = arguments.get("asn", "").strip()
            if not asn:
                raise ValueError("'asn' parameter is required.")
            result = await get_ixpdb_presence(asn)
        elif name == "gtiti_domain_intelligence":
            domain_or_operator = arguments.get("domain_or_operator", "").strip()
            if not domain_or_operator:
                raise ValueError("'domain_or_operator' parameter is required.")
            if "." in domain_or_operator and " " not in domain_or_operator:
                result = await get_domain_intelligence(domain_or_operator)
            else:
                operator_data = await lookup_operator(domain_or_operator)
                website = operator_data.get("website", "")
                if not website:
                    result = {"error": f"Could not find a website for '{domain_or_operator}' via operator lookup. Try providing a domain directly, e.g. 'tele2.com'."}
                else:
                    result = await get_domain_intelligence(website)
        elif name == "gtiti_full_briefing":
            operator_name = arguments.get("operator_name", "").strip()
            if not operator_name:
                raise ValueError("'operator_name' parameter is required.")
            fast = bool(arguments.get("fast", False))
            phase1_result, phase2_result = await asyncio.gather(lookup_operator(operator_name), build_full_briefing(operator_name, fast=fast))
            primary_asn = phase1_result.get("primary_asn", "")
            prefixes = phase1_result.get("sample_prefixes_ipv4", [])
            website = phase1_result.get("website", "")
            # Use the resolved operator name from PeeringDB rather than the raw query
            # (which might be an ASN string like "AS8708" if Claude substituted it).
            # exec_contacts only depends on this, not on primary_asn, so it belongs
            # in the same gather below instead of a serial call after it.
            resolved_name = phase1_result.get("name", operator_name)
            skipped_note = "Skipped in fast mode. Rerun gtiti_full_briefing with fast=False for the complete picture."
            if primary_asn:
                if fast:
                    bgp_classification, as_rank, as_relationships, radar_profile, prefix_ownership, exec_contacts = await asyncio.gather(
                        get_asn_classification(primary_asn),
                        get_as_rank(primary_asn),
                        get_as_relationships(primary_asn),
                        get_radar_profile(primary_asn),
                        verify_prefix_ownership(prefixes, expected_org_keyword=operator_name.split()[0] if operator_name else None),
                        get_executive_and_commercial_contacts(resolved_name),
                    )
                    routing_history = {"skipped": True, "note": skipped_note}
                    security_exposure = {"skipped": True, "note": skipped_note}
                    ixpdb_presence = {"skipped": True, "note": skipped_note}
                    domain_intel = {"skipped": True, "note": skipped_note}
                else:
                    bgp_classification, as_rank, as_relationships, radar_profile, routing_history, security_exposure, ixpdb_presence, domain_intel, prefix_ownership, exec_contacts = await asyncio.gather(
                        get_asn_classification(primary_asn),
                        get_as_rank(primary_asn),
                        get_as_relationships(primary_asn),
                        get_radar_profile(primary_asn),
                        get_routing_history(primary_asn),
                        check_exposure(prefixes),
                        get_ixpdb_presence(primary_asn),
                        get_domain_intelligence(website) if website else asyncio.sleep(0, result={"error": "No website found for this operator."}),
                        verify_prefix_ownership(prefixes, expected_org_keyword=operator_name.split()[0] if operator_name else None),
                        get_executive_and_commercial_contacts(resolved_name),
                    )
            else:
                bgp_classification = {"error": "No primary ASN found for this operator."}
                as_rank = {"error": "No primary ASN found for this operator."}
                as_relationships = {"error": "No primary ASN found for this operator."}
                radar_profile = {"error": "No primary ASN found for this operator."}
                routing_history = {"error": "No primary ASN found for this operator."}
                security_exposure = {"error": "No primary ASN found for this operator."}
                ixpdb_presence = {"error": "No primary ASN found for this operator."}
                domain_intel = {"error": "No primary ASN found for this operator."}
                prefix_ownership = {"error": "No primary ASN found for this operator."}
                exec_contacts = await get_executive_and_commercial_contacts(resolved_name)
            result = {"operator": operator_name, "fast_mode": fast, "network_data": phase1_result, "bgp_classification": bgp_classification, "as_rank": as_rank, "as_relationships": as_relationships, "cloudflare_radar": radar_profile, "routing_history": routing_history, "security_exposure": security_exposure, "ixpdb_cross_check": ixpdb_presence, "domain_intelligence": domain_intel, "prefix_ownership_verification": prefix_ownership, "executive_and_commercial_contacts": exec_contacts, **phase2_result}
        else:
            result = {"status": "error", "message": f"Unknown tool: '{name}'."}
    except Exception as e:
        result = {"status": "error", "message": str(e), "tool": name, "hint": "Check the API is reachable and your .env is configured."}
    return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

def main():
    print("🌐 GTITI MCP Server starting...", file=sys.stderr)
    print("   Phase 1: operator lookup · country operators · IXP lookup", file=sys.stderr)
    print("   Phase 2: news · crunchbase · submarine cables · contacts · full briefing", file=sys.stderr)
    print("   Phase 3: bgp.tools · bgp.he.net · CAIDA · Cloudflare Radar · routing history · Shodan · IXPDB · SecurityTrails", file=sys.stderr)
    print("   Ready. Waiting for Claude to connect...", file=sys.stderr)
    async def run():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())
    asyncio.run(run())

if __name__ == "__main__":
    main()
