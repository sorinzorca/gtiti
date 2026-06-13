import asyncio
from src.connectors.news       import get_operator_news, get_operator_investments
from src.connectors.crunchbase import get_company_intelligence
from src.connectors.submarine  import get_operator_cables
from src.connectors.contacts   import get_wholesale_contacts

async def build_full_briefing(operator_name):
    results = await asyncio.gather(
        get_operator_news(operator_name),
        get_operator_investments(operator_name),
        get_company_intelligence(operator_name),
        get_operator_cables(operator_name),
        get_wholesale_contacts(operator_name),
        return_exceptions=True,
    )
    labels = ["news", "investments", "company_intelligence", "submarine_cables", "wholesale_contacts"]
    return {
        "operator": operator_name,
        **{label: (result if not isinstance(result, Exception) else {"error": f"{label} failed: {result}"}) for label, result in zip(labels, results)},
    }
