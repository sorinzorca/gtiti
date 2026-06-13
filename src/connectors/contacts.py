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
            return [{"name": item.get("title","").split(" - ")[0].strip(), "title": item.get("title",""), "linkedin_url": item.get("link",""), "snippet": item.get("snippet",""), "source": "SerpAPI → LinkedIn"} for item in data.get("organic_results",[])[:4]]
        except Exception:
            pass
    search_url = f"https://www.google.com/search?q={quote(query)}"
    return [{"name": "(click to search manually)", "title": title_keyword, "linkedin_url": search_url, "snippet": f"Google → LinkedIn search for {title_keyword} at {company_name}", "source": "Manual search URL"}]

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
