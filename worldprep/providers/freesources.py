"""免費資料來源：維基百科（中、英文）條目全文摘錄。研究與查核先讀這些，付費的網路搜尋只用來補缺口。"""
import httpx

from ..logging_setup import log

UA = {"User-Agent": "worldprep/1.0 (beyondtravelwithus@gmail.com)"}


def wikipedia(query: str, lang: str = "zh", limit: int = 3, chars: int = 6000) -> list[dict]:
    """回傳 [{title, url, text}]；查不到或連不上就回傳空陣列。"""
    api = f"https://{lang}.wikipedia.org/w/api.php"
    try:
        hits = httpx.get(api, params={"action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json"},
                         headers=UA, timeout=20).json()["query"]["search"]
        out = []
        for h in hits:
            pages = httpx.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1, "titles": h["title"],
                                           "format": "json", "variant": "zh-tw" if lang == "zh" else None},
                              headers=UA, timeout=20).json()["query"]["pages"]
            text = next(iter(pages.values())).get("extract", "")[:chars]
            if text:
                out.append({"title": h["title"], "url": f"https://{lang}.wikipedia.org/wiki/{h['title'].replace(' ', '_')}", "text": text})
        return out
    except Exception as e:
        log.warning("wikipedia_failed", extra={"query": query, "lang": lang, "err": str(e)[:200]})
        return []


def gather(queries: list[tuple[str, str]], per_query: int = 2) -> list[dict]:
    """queries: [(查詢字, 語言)]，去除重複條目。"""
    seen, out = set(), []
    for q, lang in queries:
        for doc in wikipedia(q, lang, per_query):
            if doc["url"] not in seen:
                seen.add(doc["url"])
                out.append(doc)
    return out


def as_text(docs: list[dict]) -> str:
    return "\n\n".join(f"### {d['title']}（{d['url']}）\n{d['text']}" for d in docs)
