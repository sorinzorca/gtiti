"""
GTITI MCP Server
=================
This is the MAIN FILE. It creates the MCP server that Claude connects to.

MCP (Model Context Protocol) is Anthropic's open standard for connecting
Claude to external tools and data sources. Think of it like a USB interface —
this server is the "USB device" and Claude is the "computer" it plugs into.

When Claude is connected to this server, it can call our tools just like
it calls any of its built-in capabilities — web search, file reading, etc.

How it works:
  1. Claude Desktop (or Claude.ai with MCP support) starts this server.
  2. Claude asks "what tools do you have?" → we describe our tools.
  3. User asks Claude a question about telecom.
  4. Claude decides to call one of our tools.
  5. We run the tool, hit the APIs, return structured data.
  6. Claude synthesizes the data into a natural language answer.
"""

import asyncio
import json
import sys
from pathlib import Path

# Load .env file before anything else so our API keys are available
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# MCP SDK — this is the library that handles the protocol
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

# Our own tools
from src.tools.operator import lookup_operator, operators_in_country, lookup_ixp


# ── Create the server ──────────────────────────────────────────────────────

# "Server" is the MCP server object. The name we give it is how it
# identifies itself to Claude.
app = Server("gtiti")


# ── Declare our tools ──────────────────────────────────────────────────────
# This is like a menu we hand to Claude. Claude reads it and knows what
# it can ask us to do.

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """
    Tell Claude which tools GTITI offers.
    Claude reads these descriptions and uses them to decide WHEN to call each tool.
    The descriptions are important — write them like you're explaining to an
    intelligent colleague what each function does.
    """
    return [
        types.Tool(
            name="gtiti_operator_lookup",
            description=(
                "Look up detailed intelligence on a telecom operator / network / ISP / carrier. "
                "Returns: ASN(s), IXP presence, peering policy, peering contacts (NOC, peering team), "
                "facility presence, BGP prefix counts, upstream providers, and PeeringDB link. "
                "Use this when asked about a specific company: 'tell me about Deutsche Telekom', "
                "'what ASNs does Tele2 have', 'who handles peering at NTT Communications', "
                "'is Vivo present at IX.br São Paulo'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Operator name, ASN, or both. Examples: "
                            "'Deutsche Telekom', 'AS3320', 'Vivo Brazil', "
                            "'NTT Communications', 'MTN Nigeria', 'AS10429'"
                        ),
                    }
                },
                "required": ["query"],
            },
        ),

        types.Tool(
            name="gtiti_country_operators",
            description=(
                "Find the most important telecom operators / ISPs / carriers in a specific country. "
                "Returns a ranked list by network size (IPv4 prefix count), with ASNs, "
                "peering policy, and PeeringDB presence. "
                "Use this when asked: 'who are the main telecoms in Brazil', "
                "'most important ISPs in Germany', 'telecom operators in Nigeria', "
                "'which networks are dominant in Japan'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "country_code": {
                        "type": "string",
                        "description": (
                            "ISO 2-letter country code. Examples: "
                            "DE (Germany), BR (Brazil), JP (Japan), "
                            "NG (Nigeria), IN (India), US (United States), "
                            "FR (France), GB (United Kingdom), ZA (South Africa)"
                        ),
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "How many operators to return. Default is 10, max 20.",
                        "default": 10,
                    },
                },
                "required": ["country_code"],
            },
        ),

        types.Tool(
            name="gtiti_ixp_lookup",
            description=(
                "Look up an Internet Exchange Point (IXP) and list its member networks. "
                "Returns: IXP location, member count, and list of networks present. "
                "Use this when asked: 'who is present at DE-CIX Frankfurt', "
                "'members of LINX', 'which networks peer at AMS-IX', "
                "'who is at IX.br São Paulo'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ixp_name": {
                        "type": "string",
                        "description": (
                            "IXP name or partial name. Examples: "
                            "'DE-CIX Frankfurt', 'LINX', 'AMS-IX', "
                            "'IX.br', 'JPIX', 'HKIX', 'IXPN Lagos'"
                        ),
                    }
                },
                "required": ["ixp_name"],
            },
        ),
    ]


# ── Handle tool calls ──────────────────────────────────────────────────────
# When Claude decides to call a tool, it sends a request here.
# We run the tool and return the result.

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """
    Execute a tool call from Claude.

    name:      Which tool Claude wants to run ("gtiti_operator_lookup" etc.)
    arguments: The parameters Claude is passing in ({"query": "Deutsche Telekom"})

    We return the result as a JSON string inside a TextContent object.
    Claude then reads this JSON and synthesizes a natural language answer.
    """

    try:
        # Route to the right tool function
        if name == "gtiti_operator_lookup":
            query = arguments.get("query", "").strip()
            if not query:
                raise ValueError("'query' parameter is required and cannot be empty.")
            result = await lookup_operator(query)

        elif name == "gtiti_country_operators":
            country_code = arguments.get("country_code", "").strip().upper()
            if not country_code:
                raise ValueError("'country_code' parameter is required.")
            top_n = min(int(arguments.get("top_n", 10)), 20)   # Cap at 20
            result = await operators_in_country(country_code, top_n)

        elif name == "gtiti_ixp_lookup":
            ixp_name = arguments.get("ixp_name", "").strip()
            if not ixp_name:
                raise ValueError("'ixp_name' parameter is required.")
            result = await lookup_ixp(ixp_name)

        else:
            result = {
                "status": "error",
                "message": f"Unknown tool: '{name}'. "
                           f"Available tools: gtiti_operator_lookup, "
                           f"gtiti_country_operators, gtiti_ixp_lookup",
            }

    except Exception as e:
        # If anything goes wrong, return a structured error instead of crashing
        result = {
            "status":  "error",
            "message": str(e),
            "tool":    name,
            "hint":    "Check the API is reachable and your .env is configured.",
        }

    # Return the result as a JSON string
    # Claude will parse this and turn it into a natural language response
    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, indent=2, ensure_ascii=False),
        )
    ]


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    """
    Start the GTITI MCP server.

    MCP servers communicate over stdin/stdout (the same way your terminal
    pipes data between programs). Claude Desktop starts this process and
    talks to it through those pipes.
    """
    print("🌐 GTITI MCP Server starting...", file=sys.stderr)
    print("   Tools: operator lookup · country operators · IXP lookup", file=sys.stderr)
    print("   Data:  PeeringDB · RIPE NCC RIPEstat", file=sys.stderr)
    print("   Ready. Waiting for Claude to connect...", file=sys.stderr)

    async def run():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())
    asyncio.run(run())


if __name__ == "__main__":
    main()
