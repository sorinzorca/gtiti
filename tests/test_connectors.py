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

from src.connectors import peeringdb, ripe, submarine, caida, cloudflare_radar
from src.tools.operator import lookup_operator, operators_in_country
from src.tools.briefing import build_full_briefing


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
    header("Country operators: Brazil (BR) — now CAIDA rank-scan based")
    result = await operators_in_country("BR", top_n=5)
    assert result["status"] == "ok"
    ops = result.get("operators", [])
    ok(f"Top {len(ops)} operators in Brazil:")
    for op in ops:
        pdb = "✓ PeeringDB" if op.get("in_peeringdb") else "  no PeeringDB"
        info(f"  {op['asn']:12}  {op['name'][:40]:40}  rank={op.get('global_rank','?'):5}  {pdb}")
    assert all("global_rank" in o for o in ops), "Expected CAIDA-sourced results with a global_rank field"
    ranks = [o["global_rank"] for o in ops]
    assert ranks == sorted(ranks), "Operators should be returned in ascending (best-first) rank order"
    assert "CAIDA AS Rank" in result["data_sources"][0]


async def test_country_operators_deterministic():
    header("Country operators: same query twice should give identical results")
    r1 = await operators_in_country("DE", top_n=5)
    r2 = await operators_in_country("DE", top_n=5)
    names1 = [o["name"] for o in r1["operators"]]
    names2 = [o["name"] for o in r2["operators"]]
    assert names1 == names2, "CAIDA rank-scan should be deterministic, unlike the old RIPEstat sampling"
    ok(f"Same top-5 for Germany on both runs: {names1}")


async def test_submarine_matching_no_short_string_false_positives():
    header("Submarine matching: pure unit checks against the 'e&'/'digi' false-positive bugs")
    cases = [
        ("Tele2 Sweden", "TDC Group, Tele2", True),
        ("Tele2 Sweden", "Tele2, Tet", True),
        ("Tele2 Sweden", "G42, Mobily, TeleYemen, e&", False),
        ("Tele2 Sweden", "Ooredoo, e&", False),
        ("DIGI Romania", "Digicel", False),
        ("DIGI Romania", "BW Digital", False),
        ("DIGI Romania", "Valencia Digital Port Connect", False),
        ("DIGI Romania", "RCS & RDS", True),
        ("GlobalConnect", "GlobalConnect", True),
    ]
    for operator, owners, expected in cases:
        got = submarine._operator_matches(operator, owners)
        assert got == expected, f"{operator!r} vs {owners!r} -> {got}, expected {expected}"
    ok(f"All {len(cases)} matching cases behave correctly")


async def test_submarine_country_lookup():
    header("Submarine cables: cables landing in Sweden")
    result = await submarine.get_cables_by_country("Sweden")
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    ok(f"Found {result['total_cables']} cables landing in Sweden")
    assert result["total_cables"] > 0, "Sweden should have several known cable landings (NordBalt, Baltic Sea Submarine Cable, etc.)"
    for c in result["cables"][:3]:
        info(f"  {c['cable_name']}  (owners: {', '.join(c['owners']) or 'unknown'})")


async def test_submarine_operator_with_known_cables():
    header("Submarine cables: operator with known real memberships (Telia)")
    result = await submarine.get_operator_cables("Telia")
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    ok(f"Telia cable memberships: {result['total_cables']}")
    assert result["total_cables"] > 0, "Telia is a known Nordic cable owner — should have at least one match"
    if result["cable_memberships"]:
        info(f"  e.g. {result['cable_memberships'][0]['cable_name']}")


async def test_submarine_operator_tele2_real_matches_only():
    header("Submarine cables: Tele2 Sweden's real memberships (regression for the 'e&' false-positive bug)")
    result = await submarine.get_operator_cables("Tele2 Sweden")
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    names = {m["cable_name"] for m in result["cable_memberships"]}
    ok(f"Tele2 Sweden cable memberships: {sorted(names)}")
    # Tele2 genuinely co-owns these two short Baltic Sea links (both list "Tele2"
    # directly in TeleGeography's owners field) - these should always match.
    assert "Denmark-Sweden 17" in names, "Tele2 is a real co-owner of Denmark-Sweden 17"
    assert "Latvia-Sweden 1 (LV-SE 1)" in names, "Tele2 is a real co-owner of Latvia-Sweden 1"
    # These previously false-positived because "e&" (Etisalat's rebrand) normalizes
    # to a single character that's trivially "contained in" almost anything.
    false_positive_cables = {"Africa-1", "Asia Africa Europe-1 (AAE-1)", "Qatar-U.A.E. Submarine Cable System"}
    assert not (names & false_positive_cables), f"False-positive 'e&' matches leaked back in: {names & false_positive_cables}"
    ok("No 'e&' false positives leaked through")


async def test_submarine_operator_digi_romania_no_false_positives():
    header("Submarine cables: DIGI Romania (regression for the bare-'digi'-alias false-positive bug)")
    result = await submarine.get_operator_cables("DIGI Romania")
    assert "error" not in result, f"Should not error, got: {result.get('error')}"
    names = {m["cable_name"] for m in result["cable_memberships"]}
    # These previously false-positived because the bare "digi" alias substring-matched
    # into any unrelated company name containing "digi" (Digicel, BW Digital, etc.)
    false_positive_owners = {"Digicel", "BW Digital", "Valencia Digital Port Connect"}
    for m in result["cable_memberships"]:
        assert not (set(m["all_owners"]) & false_positive_owners), f"'{m['cable_name']}' is a false positive (owners: {m['all_owners']})"
    ok(f"DIGI Romania: {result['total_cables']} memberships, none are Digicel/BW Digital/etc false positives")


async def test_caida_as_relationships_parallel_pagination():
    header("CAIDA: AS relationships for AS3320 (parallelized pagination)")
    result = await caida.get_as_relationships(3320)
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    ok(f"Total relationships: {result['total_relationships']} (customers={result['customer_count']}, providers={result['provider_count']}, peers={result['peer_count']})")
    assert result["total_relationships"] > 100, "AS3320 (Deutsche Telekom) should have well over 100 relationships"
    assert result["customer_count"] + result["provider_count"] + len(result["peers"]) > 0


async def test_cloudflare_radar_concurrent_calls():
    header("Cloudflare Radar: concurrent profile fetch for AS3320")
    result = await cloudflare_radar.get_radar_profile(3320)
    if not result.get("available", True) and "CF_API_TOKEN" in result.get("message", ""):
        info("CF_API_TOKEN not set — skipping (connector correctly reports unavailable)")
        return
    assert "error" not in result, f"Unexpected error: {result.get('error')}"
    ok(f"RPKI valid_pct: {result['rpki_validation'].get('valid_pct')}")


async def test_full_briefing_fast_mode():
    header("Full briefing: fast=True skips submarine cables")
    result = await build_full_briefing("Tele2", fast=True)
    assert result["submarine_cables"].get("skipped") is True, "fast=True should skip submarine cable lookup"
    ok("fast=True correctly skipped the submarine cable index build")

    header("Full briefing: fast=False includes submarine cables")
    result = await build_full_briefing("Tele2", fast=False)
    assert "skipped" not in result["submarine_cables"], "fast=False should actually run the submarine cable lookup"
    ok("fast=False correctly ran the submarine cable lookup")


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
        ("Country operators: deterministic", test_country_operators_deterministic),
        ("Submarine: matching unit checks", test_submarine_matching_no_short_string_false_positives),
        ("Submarine: country lookup", test_submarine_country_lookup),
        ("Submarine: known operator", test_submarine_operator_with_known_cables),
        ("Submarine: Tele2 real matches only", test_submarine_operator_tele2_real_matches_only),
        ("Submarine: DIGI Romania no false positives", test_submarine_operator_digi_romania_no_false_positives),
        ("CAIDA: parallel pagination", test_caida_as_relationships_parallel_pagination),
        ("Cloudflare Radar: concurrent calls", test_cloudflare_radar_concurrent_calls),
        ("Full briefing: fast mode",  test_full_briefing_fast_mode),
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
