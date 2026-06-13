# GTITI — Global Telecom Intelligence MCP Server

## What this is

GTITI is a **Claude tool** (called an MCP server).

When you connect it to Claude and ask something like:
> "Who are the most important telecom operators in Brazil, what are their ASNs, and who handles peering?"

Claude will **automatically call GTITI**, which fans out to multiple data sources
(PeeringDB, RIPE, web search, etc.), collects the answers, and gives you one
clean response — instead of you spending 45 minutes across 6 browser tabs.

---

## Project structure

```
gtiti/
├── src/
│   ├── server.py          ← The main MCP server. Claude talks to this.
│   ├── tools/
│   │   └── operator.py    ← The tool Claude calls: "look up a telecom operator"
│   └── connectors/
│       ├── peeringdb.py   ← Fetches ASN + IXP data from PeeringDB (free API)
│       └── ripe.py        ← Fetches BGP prefix data from RIPE NCC (free API)
├── tests/
│   └── test_connectors.py ← Simple tests to verify the APIs are working
├── .env.example           ← Template for your API keys
├── pyproject.toml         ← Project config + dependencies
└── README.md              ← This file
```

---

## How to run it (step by step)

### 1. Copy the environment file
```bash
cp .env.example .env
```

### 2. (Optional) Add API keys to .env
The PeeringDB and RIPE connectors work without keys.
For LinkedIn / Crunchbase you'll need keys later — leave blank for now.

### 3. Run the server
```bash
uv run gtiti
```

### 4. Connect it to Claude Desktop
Add this to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "gtiti": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/gtiti", "gtiti"]
    }
  }
}
```

Then restart Claude Desktop and ask:
> "Use GTITI to look up Deutsche Telekom"

---

## Data sources (current)

| Source     | What it provides              | Auth needed? |
|------------|-------------------------------|--------------|
| PeeringDB  | ASNs, IXP presence, contacts  | No (free)    |
| RIPE NCC   | BGP prefixes, WHOIS           | No (free)    |

## Data sources (coming next)

| Source      | What it will provide          |
|-------------|-------------------------------|
| Crunchbase  | Investments, M&A, funding     |
| LinkedIn    | Key contacts, org charts      |
| Web search  | News, press releases          |
