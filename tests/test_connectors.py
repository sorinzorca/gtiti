"""
GTITI Connector Tests
======================
These tests hit the REAL APIs (PeeringDB, RIPE) to verify everything works.
They are NOT mock tests — they make real network requests.

Run them with:
    uv run python -m pytest tests/ -v

Or just:
    uv run python tests/test_connectors.py
"""

import asyncio
import sys
import os

# Make sure Python can find our src/ folder
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from src.connectors import peeringdb, ripe
from src.tools.operator import lookup_operator, operators_in_country


# ── Colour helpers for terminal output ────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):  print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg): print(f"  {RED}✗{RESET} {msg}")
def info(msg): print(f"  {BLUE}→{RESET} {msg}")
def header(msg): print(f"\n{BOLD}{YELLOW}━━ {msg} ━━{RESET}")


# ── Individual tests ───────────────────────────────────────────────────────

async def test_peeringdb_search():
    header("PeeringDB: search by name")
    results = await peeringdb.search_networks("Deutsche Telekom")
    assert len(results) > 0, "Should find at least one result"
    first = results[0]
    ok(f"Found: {first['name']}  (ASN {first['asn']})")
    assert first["asn"] is not None, "ASN should not be None"
    assert "peeringdb_id" in first
    ok(f"Has PeeringDB ID: {first['peeringdb_id']}")
    return first["peeringdb_id"], first["asn"]


async def test_peeringdb_asn_search():
    header("PeeringDB: search by ASN")
    results = await peeringdb.search_networks("AS3320")
    assert len(results) > 0, "AS3320 should be in PeeringDB"
    first = results[0]
    ok(f"Found by ASN: {first['name']}")
    assert first["asn"] == 3320, f"Expected ASN 3320, got {first['asn']}"
    ok("ASN match confirmed")


async def test_peeringdb_details(pdb_id: int):
    header(f"PeeringDB: full details for ID {pdb_id}")
    details = await peeringdb.get_network_details(pdb_id)

    ok(f"Name: {details['name']}")
    ok(f"Peering policy: {details['peering_policy']}")

    ixps = details.get("ixp_presence", [])
    ok(f"IXP presence: {len(ixps)} exchanges")
    if ixps:
        top = ixps[0]
        ok(f"  Largest port: {top['ixp_name']} @ {top['speed_human']}")

    contacts = details.get("contacts", [])
    ok(f"Contacts: {len(contacts)} found")
    for c in contacts[:3]:
        info(f"  {c['role']}: {c['name']}  ({c.get('email', 'no email')})")

    facilities = details.get("facilities", [])
    ok(f"Facilities: {len(facilities)} colo locations")


async def test_ripe_overview():
    header("RIPE NCC: ASN overview for AS3320")
    overview = await ripe.get_asn_overview(3320)
    ok(f"Holder: {overview['name']}")
    assert overview["announced"] == True, "AS3320 should be announced"
    ok("AS3320 is announced (active in BGP)")


async def test_ripe_prefixes():
    header("RIPE NCC: announced prefixes for AS3320")
    prefixes = await ripe.get_announced_prefixes(3320)
    ok(f"IPv4 prefixes: {prefixes['ipv4_count']}")
    ok(f"IPv6 prefixes: {prefixes['ipv6_count']}")
    assert prefixes["ipv4_count"] > 0, "DT should have IPv4 prefixes"
    if prefixes["ipv4_prefixes"]:
        ok(f"Sample prefix: {prefixes['ipv4_prefixes'][0]}")


async def test_ripe_upstreams():
    header("RIPE NCC: upstream providers for AS3320")
    upstreams = await ripe.get_upstreams(3320)
    ups = upstreams.get("upstreams", [])
    ok(f"Upstream count: {len(ups)}")
    for u in ups[:3]:
        info(f"  {u['asn']}  (power: {u['power']})")


async def test_ripe_country():
    header("RIPE NCC: ASNs in Germany (DE)")
    asns = await ripe.get_country_asns("DE")
    assert isinstance(asns, list), "Should return a list"
    ok(f"ASNs found in DE: {len(asns)}")
    ok("Top 3 by prefix count:")
    for a in asns[:3]:
        info(f"  {a['asn']}  {a['name']}  ({a['ipv4_prefixes']} IPv4 prefixes)")


async def test_full_operator_lookup():
    header("Full operator lookup: 'NTT Communications'")
    result = await lookup_operator("NTT Communications")
    assert result["status"] == "ok", f"Expected ok, got: {result}"
    ok(f"Name: {result['name']}")
    ok(f"Primary ASN: {result['primary_asn']}")
    ok(f"IPv4 prefixes: {result['ipv4_prefixes_announced']}")
    ok(f"IXP count: {result['ixp_count']}")
    ok(f"Peering policy: {result['peering_policy']}")
    if result.get("upstreams"):
        ok(f"Upstreams found: {len(result['upstreams'])}")


async def test_country_operators():
    header("Country operators: Brazil (BR)")
    result = await operators_in_country("BR", top_n=5)
    assert result["status"] == "ok"
    ops = result.get("operators", [])
    ok(f"Top {len(ops)} operators in Brazil:")
    for op in ops:
        pdb = "✓ PeeringDB" if op.get("in_peeringdb") else "  no PeeringDB"
        info(f"  {op['asn']:12}  {op['name'][:40]:40}  {pdb}")


async def test_not_found():
    header("Graceful not-found handling")
    result = await lookup_operator("XYZZY_NONEXISTENT_OPERATOR_12345")
    assert result["status"] in ("not_found", "ok"), f"Got unexpected status: {result['status']}"
    if result["status"] == "not_found":
        ok(f"Got not_found status as expected")
    else:
        ok(f"Got a result (PeeringDB partial match) — acceptable")


# ── Run all tests ──────────────────────────────────────────────────────────

async def run_all():
    print(f"\n{BOLD}GTITI Connector Tests{RESET}")
    print("Testing live connections to PeeringDB and RIPE NCC...\n")

    passed = 0
    failed = 0

    tests = [
        ("PeeringDB search",         test_peeringdb_search),
        ("PeeringDB ASN search",     test_peeringdb_asn_search),
        ("RIPE ASN overview",        test_ripe_overview),
        ("RIPE prefixes",            test_ripe_prefixes),
        ("RIPE upstreams",           test_ripe_upstreams),
        ("RIPE country ASNs",        test_ripe_country),
        ("Full operator lookup",     test_full_operator_lookup),
        ("Country operators",        test_country_operators),
        ("Not found handling",       test_not_found),
    ]

    # Special handling for the test that returns values used by the next test
    try:
        pdb_id, asn = await test_peeringdb_search()
        await test_peeringdb_details(pdb_id)
        passed += 2
    except Exception as e:
        fail(f"PeeringDB detail test failed: {e}")
        failed += 2

    for test_name, test_fn in tests[1:]:   # Skip the first one (already ran above)
        try:
            await test_fn()
            passed += 1
        except AssertionError as e:
            fail(f"{test_name}: ASSERTION FAILED — {e}")
            failed += 1
        except Exception as e:
            fail(f"{test_name}: ERROR — {type(e).__name__}: {e}")
            failed += 1

    # Summary
    total = passed + failed
    print(f"\n{'━'*40}")
    if failed == 0:
        print(f"{GREEN}{BOLD}All {passed}/{total} tests passed ✓{RESET}")
    else:
        print(f"{YELLOW}{BOLD}{passed}/{total} passed{RESET}, {RED}{failed} failed{RESET}")
    print()

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)
