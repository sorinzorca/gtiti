import asyncio
from src.connectors.news       import get_operator_news, get_operator_investments
from src.connectors.crunchbase import get_company_intelligence
from src.connectors.submarine  import get_operator_cables
from src.connectors.contacts   import get_wholesale_contacts

_SKIPPED_NOTE = (
    "Skipped in fast mode. Submarine cable data requires building/refreshing a "
    "~600-cable index, which can add several seconds on a cold cache (up to once "
    "every 24h). Call gtiti_submarine_cables directly, or rerun gtiti_full_briefing "
    "with fast=False, for cable memberships."
)

async def build_full_briefing(operator_name, fast=False):
    tasks   = [get_operator_news(operator_name), get_operator_investments(operator_name), get_company_intelligence(operator_name), get_wholesale_contacts(operator_name)]
    labels  = ["news", "investments", "company_intelligence", "wholesale_contacts"]
    if not fast:
        tasks.append(get_operator_cables(operator_name))
        labels.append("submarine_cables")

    results = await asyncio.gather(*tasks, return_exceptions=True)
    output = {
        "operator": operator_name,
        **{label: (result if not isinstance(result, Exception) else {"error": f"{label} failed: {result}"}) for label, result in zip(labels, results)},
    }
    if fast:
        output["submarine_cables"] = {"skipped": True, "note": _SKIPPED_NOTE}
    return output
