import os
import asyncio
import aiohttp

CRUNCHBASE_KEY = os.getenv("CRUNCHBASE_API_KEY", "")
CB_BASE        = "https://api.crunchbase.com/api/v4"

async def _cb_get(path, params):
    params["user_key"] = CRUNCHBASE_KEY
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{CB_BASE}{path}", params=params, timeout=aiohttp.ClientTimeout(total=12)) as r:
            r.raise_for_status()
            return await r.json()

async def _find_permalink(company_name):
    data = await _cb_get("/autocompletes", {"query": company_name, "collection_ids": "organizations", "limit": 5})
    entities = data.get("entities", [])
    if not entities:
        return None
    return entities[0].get("identifier", {}).get("permalink")

async def _get_overview(permalink):
    fields = "identifier,short_description,website,num_employees_enum,revenue_range,total_funding_usd,last_funding_type,last_funding_at,num_funding_rounds,num_acquisitions,stock_symbol,ipo_status"
    data = await _cb_get(f"/entities/organizations/{permalink}", {"field_ids": fields})
    return data.get("properties", {})

async def _get_funding_rounds(permalink, limit=5):
    try:
        data = await _cb_get(f"/entities/organizations/{permalink}/funding_rounds", {"field_ids": "announced_at,investment_type,money_raised,investors", "limit": limit})
        rounds = []
        for item in data.get("entities", []):
            p = item.get("properties", {})
            rounds.append({"date": p.get("announced_at",""), "type": p.get("investment_type",""), "amount_usd": p.get("money_raised",{}).get("value_usd",""), "investors": [inv.get("identifier",{}).get("value","") for inv in p.get("investors",[])]})
        return rounds
    except Exception:
        return []

async def _get_acquisitions(permalink, limit=5):
    try:
        data = await _cb_get(f"/entities/organizations/{permalink}/acquisitions", {"field_ids": "announced_on,acquiree_identifier,price", "limit": limit})
        return [{"date": item.get("properties",{}).get("announced_on",""), "company_acquired": item.get("properties",{}).get("acquiree_identifier",{}).get("value",""), "price_usd": item.get("properties",{}).get("price",{}).get("value_usd","N/A")} for item in data.get("entities",[])]
    except Exception:
        return []

async def _get_executives(permalink, limit=6):
    try:
        data = await _cb_get(f"/entities/organizations/{permalink}/current_employees", {"field_ids": "identifier,primary_job_title", "limit": limit})
        return [{"name": item.get("properties",{}).get("identifier",{}).get("value",""), "title": item.get("properties",{}).get("primary_job_title","")} for item in data.get("entities",[])]
    except Exception:
        return []

async def get_company_intelligence(company_name):
    if not CRUNCHBASE_KEY:
        return {"company": company_name, "available": False, "message": "CRUNCHBASE_API_KEY not set. Add to .env. Free trial at data.crunchbase.com"}
    try:
        permalink = await _find_permalink(company_name)
        if not permalink:
            return {"company": company_name, "available": False, "message": f"'{company_name}' not found in Crunchbase."}
        overview, rounds, acquisitions, executives = await asyncio.gather(_get_overview(permalink), _get_funding_rounds(permalink), _get_acquisitions(permalink), _get_executives(permalink))
        return {"company": company_name, "available": True, "permalink": permalink, "overview": {"description": overview.get("short_description",""), "website": overview.get("website",{}).get("value",""), "employees": overview.get("num_employees_enum",""), "revenue_range": overview.get("revenue_range",""), "total_funding": overview.get("total_funding_usd",""), "last_funding_type": overview.get("last_funding_type",""), "last_funding_date": overview.get("last_funding_at",""), "num_acquisitions": overview.get("num_acquisitions",0), "ipo_status": overview.get("ipo_status",""), "stock_symbol": overview.get("stock_symbol","")}, "funding_rounds": rounds, "acquisitions": acquisitions, "executives": executives, "crunchbase_url": f"https://www.crunchbase.com/organization/{permalink}"}
    except aiohttp.ClientResponseError as exc:
        msg = "Crunchbase API key invalid or expired." if exc.status == 401 else f"Crunchbase API error {exc.status}"
        return {"company": company_name, "available": False, "message": msg}
    except Exception as exc:
        return {"company": company_name, "available": False, "message": str(exc)}
