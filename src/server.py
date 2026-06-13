"""
GTITI MCP Server — Phase 1 + Phase 2
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
from src.connectors.contacts import get_wholesale_contacts

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
            name="gtiti_full_briefing",
            description="Generate a COMPLETE operator briefing combining ALL data sources: ASNs, IXP presence, BGP prefixes, peering contacts, recent news, Crunchbase financials, submarine cable memberships, and wholesale LinkedIn contacts. Use this when asked: 'Prepare a full briefing on Tele2 Sweden', 'I have a meeting with Deutsche Telekom, give me everything', 'complete profile of NTT Communications'.",
            inputSchema={"type": "object", "properties": {"operator_name": {"type": "string", "description": "Operator name. Examples: 'Tele2 Sweden', 'Deutsche Telekom', 'NTT Communications'"}}, "required": ["operator_name"]},
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
        elif name == "gtiti_full_briefing":
            operator_name = arguments.get("operator_name", "").strip()
            if not operator_name:
                raise ValueError("'operator_name' parameter is required.")
            phase1_result, phase2_result = await asyncio.gather(lookup_operator(operator_name), build_full_briefing(operator_name))
            result = {"operator": operator_name, "network_data": phase1_result, **phase2_result}
        else:
            result = {"status": "error", "message": f"Unknown tool: '{name}'."}
    except Exception as e:
        result = {"status": "error", "message": str(e), "tool": name, "hint": "Check the API is reachable and your .env is configured."}
    return [types.TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]

def main():
    print("🌐 GTITI MCP Server starting...", file=sys.stderr)
    print("   Phase 1: operator lookup · country operators · IXP lookup", file=sys.stderr)
    print("   Phase 2: news · crunchbase · submarine cables · contacts · full briefing", file=sys.stderr)
    print("   Ready. Waiting for Claude to connect...", file=sys.stderr)
    async def run():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())
    asyncio.run(run())

if __name__ == "__main__":
    main()
