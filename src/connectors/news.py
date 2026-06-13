import os
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote

SERPAPI_KEY   = os.getenv("SERPAPI_KEY", "")
NEWS_RSS_BASE = "https://news.google.com/rss/search"
_BROWSER_UA   = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

async def _fetch_serpapi(query, num=8):
    params = {"q": query, "tbm": "nws", "num": num, "api_key": SERPAPI_KEY, "hl": "en", "gl": "us"}
    async with aiohttp.ClientSession() as s:
        async with s.get("https://serpapi.com/search", params=params, timeout=aiohttp.ClientTimeout(total=10)) as r:
            r.raise_for_status()
            data = await r.json()
    return [{"title": i.get("title",""), "source": i.get("source",""), "date": i.get("date",""), "snippet": i.get("snippet",""), "link": i.get("link","")} for i in data.get("news_results",[])]

async def _fetch_rss(url, source_label, num=8):
    headers = {"User-Agent": _BROWSER_UA, "Accept": "application/rss+xml, */*"}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=12)) as r:
            r.raise_for_status()
            text = await r.text()
    root = ET.fromstring(text)
    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else []
    results = []
    for item in items[:num]:
        results.append({"title": item.findtext("title","").split(" - ")[0].strip(), "source": source_label, "date": item.findtext("pubDate",""), "snippet": (item.findtext("description","") or "")[:200], "link": item.findtext("link","")})
    return results

async def _fetch_news(query, num=8):
    if SERPAPI_KEY:
        try:
            return await _fetch_serpapi(query, num), "SerpAPI"
        except Exception:
            pass
    try:
        url = f"{NEWS_RSS_BASE}?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
        return await _fetch_rss(url, "Google News", num), "Google News RSS"
    except Exception:
        pass
    try:
        url = f"https://www.bing.com/news/search?q={quote(query)}&format=rss&setmkt=en-US"
        return await _fetch_rss(url, "Bing News", num), "Bing News RSS"
    except Exception as exc:
        raise RuntimeError(f"All news backends failed: {exc}. Set SERPAPI_KEY in .env (free 100/mo at serpapi.com).")

async def get_operator_news(operator_name, months_back=6):
    year = datetime.utcnow().year
    query = f"{operator_name} telecom investment acquisition news {year}"
    try:
        articles, backend = await _fetch_news(query)
    except Exception as exc:
        return {"operator": operator_name, "articles": [], "article_count": 0, "query_used": query, "source_backend": "error", "error": str(exc), "fix": "Set SERPAPI_KEY in .env"}
    return {"operator": operator_name, "articles": articles, "article_count": len(articles), "query_used": query, "source_backend": backend}

async def get_operator_investments(operator_name):
    year = datetime.utcnow().year
    query = f"{operator_name} acquisition merger investment funding {year}"
    try:
        articles, backend = await _fetch_news(query, num=6)
    except Exception as exc:
        return {"operator": operator_name, "articles": [], "article_count": 0, "query_used": query, "source_backend": "error", "error": str(exc)}
    return {"operator": operator_name, "articles": articles, "article_count": len(articles), "query_used": query, "source_backend": backend}
