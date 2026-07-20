from typing import Any
import os
import httpx

async def brave_web_search(query: str, count: int = 5) -> str:
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return "Web search is disabled because BRAVE_API_KEY is missing."

    headers = {
        "X-Subscription-Token": api_key,
        "Accept": "application/json"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers=headers,
                params={"q": query, "count": count}
            )
            
        if response.status_code == 429:
            return "Web search is temporarily unavailable (rate limited)."
        if response.status_code >= 400:
            return f"Web search failed: {response.text}"
            
        data = response.json()
        results = data.get("web", {}).get("results", [])
        
        if not results:
            return "No results found on the web."
            
        formatted_results = []
        for i, res in enumerate(results, 1):
            title = res.get("title", "")
            desc = res.get("description", "")
            url = res.get("url", "")
            formatted_results.append(f"Source [{i}]: {title}\nURL: {url}\nSummary: {desc}")
            
        return "\n\n".join(formatted_results)
    except Exception as exc:
        return f"Web search encountered an error: {str(exc)}"
