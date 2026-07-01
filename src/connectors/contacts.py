import os
import asyncio
import aiohttp
from urllib.parse import quote

RAPIDAPI_KEY  = os.getenv("RAPIDAPI_KEY", "")
SERPAPI_KEY   = os.getenv("SERPAPI_KEY", "")
RAPIDAPI_HOST = "fresh-linkedin-profile-data.p.rapidapi.com"
_DEFAULT_ROLES = ["VP Wholesale", "Head of Peering", "Carrier Relations", "Director International"]

async def _rapidapi_search(company_name, title_keyword, limit=3):
    url = f"https://{RAPIDAPI_HOST}/search-employees"
    headers = {"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST}
    params = {"company_name": company_name, "keyword": title_keyword, "page": "1"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=12)) as r:
            r.raise_for_status()
            data = await r.json()
    return [{"name": item.get("full_name",""), "title": item.get("headline",""), "linkedin_url": item.get("profile_url",""), "location": item.get("location",""), "source": "RapidAPI LinkedIn"} for item in (data.get("data") or [])[:limit]]

async def _google_linkedin_search(company_name, title_keyword):
    query = f'site:linkedin.com/in "{company_name}" "{title_keyword}"'
    if SERPAPI_KEY:
        params = {"q": query, "num": 4, "api_key": SERPAPI_KEY}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("https://serpapi.com/search", params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    r.raise_for_status()
                    data = await r.json()
            results = [{"name": item.get("title","").split(" - ")[0].strip(), "title": item.get("title",""), "linkedin_url": item.get("link",""), "snippet": item.get("snippet",""), "source": "SerpAPI -> LinkedIn (direct profile link)"} for item in data.get("organic_results",[])[:4]]
            # Only return SerpAPI results if they actually resolved to linkedin.com/in/ URLs
            results = [r for r in results if "linkedin.com/in/" in r.get("linkedin_url", "")]
            if results:
                return results
        except Exception:
            pass
    # Fallback: LinkedIn's own public people-search URL, no key needed.
    # This goes directly into LinkedIn's search results, not a Google detour.
    linkedin_keywords = quote(f'{company_name} {title_keyword}')
    search_url = f"https://www.linkedin.com/search/results/people/?keywords={linkedin_keywords}"
    return [{"name": "(open LinkedIn search results)", "title": title_keyword, "linkedin_url": search_url, "snippet": f"Direct LinkedIn people-search for '{title_keyword}' at {company_name}", "source": "LinkedIn people search (no key needed)"}]

async def get_wholesale_contacts(company_name, roles=None):
    if roles is None:
        roles = _DEFAULT_ROLES
    contacts = []
    if RAPIDAPI_KEY:
        results = await asyncio.gather(*[_rapidapi_search(company_name, role) for role in roles], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                contacts.extend(r)
        seen, deduped = set(), []
        for c in contacts:
            key = c.get("linkedin_url","")
            if key and key not in seen:
                seen.add(key)
                deduped.append(c)
        contacts = deduped
        method = "RapidAPI LinkedIn (live profiles)"
    else:
        results = await asyncio.gather(*[_google_linkedin_search(company_name, role) for role in roles[:4]], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                contacts.extend(r)
        method = "SerpAPI → LinkedIn search results" if SERPAPI_KEY else "Pre-built Google search URLs (no API key)"
    note = "" if RAPIDAPI_KEY else "For live LinkedIn profiles add RAPIDAPI_KEY to .env. The URLs above are ready to click."
    return {"company": company_name, "contacts": contacts, "contact_count": len(contacts), "method": method, "note": note}


_EXECUTIVE_ROLES = ["CEO", "CTO", "COO"]
_B2B_ROLES = ["VP Wholesale", "Head of Peering", "Carrier Relations", "Director International"]
_COMPLIANCE_ROLES = ["CISO", "DPO", "Head of Compliance", "Head of Legal", "Compliance Officer", "Head of Cybersecurity"]


async def get_executive_and_commercial_contacts(company_name, include_executives=True, include_b2b=True, include_compliance=True):
    """
    Find CEO/CTO/COO, B2B/wholesale/peering, and compliance/security contacts for a company.
    Always returns clickable LinkedIn search links or live profile URLs -
    never imports or reproduces LinkedIn profile content directly.
    """
    roles = []
    if include_executives:
        roles += _EXECUTIVE_ROLES
    if include_b2b:
        roles += _B2B_ROLES
    if include_compliance:
        roles += _COMPLIANCE_ROLES

    if not roles:
        return {"company": company_name, "contacts": [], "contact_count": 0, "method": "none", "note": "Both include_executives and include_b2b were false."}

    contacts = []

    if RAPIDAPI_KEY:
        results = await asyncio.gather(*[_rapidapi_search(company_name, role) for role in roles], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                contacts.extend(r)
        seen, deduped = set(), []
        for c in contacts:
            key = c.get("linkedin_url", "")
            if key and key not in seen:
                seen.add(key)
                deduped.append(c)
        contacts = deduped
        method = "RapidAPI LinkedIn (live profiles)"
    else:
        # Cap at 8 role searches to avoid excessive parallel calls when both groups are included
        results = await asyncio.gather(*[_google_linkedin_search(company_name, role) for role in roles[:15]], return_exceptions=True)
        for r in results:
            if isinstance(r, list):
                contacts.extend(r)
        method = "SerpAPI -> LinkedIn search results" if SERPAPI_KEY else "Pre-built Google search URLs (no API key)"

    # Tag each contact with which category it was searched under, for easier grouping by the caller
    _COMPLIANCE_KEYWORDS = ["ciso", "dpo", "compliance", "legal", "cybersecurity", "information security", "data protection"]
    for c in contacts:
        role_searched = c.get("title", "").lower()
        if any(r in role_searched for r in ["ceo", "chief executive", "cto", "chief technology", "coo", "chief operating"]):
            c["category"] = "executive"
        elif any(r in role_searched for r in _COMPLIANCE_KEYWORDS):
            c["category"] = "compliance_security"
        else:
            c["category"] = "b2b_wholesale"

    note = "" if RAPIDAPI_KEY else "All results are clickable LinkedIn search links, not imported profile data. For live resolved profiles, add RAPIDAPI_KEY to .env."

    return {
        "company": company_name,
        "contacts": contacts,
        "contact_count": len(contacts),
        "executive_contacts": [c for c in contacts if c.get("category") == "executive"],
        "b2b_wholesale_contacts": [c for c in contacts if c.get("category") == "b2b_wholesale"],
        "compliance_security_contacts": [c for c in contacts if c.get("category") == "compliance_security"],
        "method": method,
        "note": note,
    }
