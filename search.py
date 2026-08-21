"""search_filings — filtered vector search over indexed 10-K narrative."""

from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue

from helpers import normalize_period

QDRANT_URL = "http://localhost:6333"
COLLECTION = "financial_docs"
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
TOP_K = 5


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("BAAI/bge-large-en-v1.5")


@lru_cache(maxsize=1)
def _client():
    return QdrantClient(url=QDRANT_URL)


def search_filings(company, question, period, top_k=TOP_K):
    year = normalize_period(period)
    ticker = company.strip().upper()

    vector = _model().encode(
        BGE_QUERY_PREFIX + question, normalize_embeddings=True
    ).tolist()

    hits = _client().query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=top_k,
        with_payload=True,
        query_filter=Filter(must=[
            FieldCondition(key="ticker", match=MatchValue(value=ticker)),
            FieldCondition(key="fiscal_year", match=MatchValue(value=year)),
        ]),
    ).points

    if not hits:
        raise ValueError(
            f"No indexed passages for {ticker} FY{year}. "
            f"Indexed filings must be ingested and embedded first."
        )

    return {
        "company": ticker,
        "fiscal_year": year,
        "question": question,
        "passages": [
            {
                "text": h.payload["text"],
                "breadcrumb": h.payload.get("breadcrumb", ""),
                "page": h.payload.get("page"),
                "source_file": h.payload.get("source_file", ""),
                "score": round(h.score, 4),
            }
            for h in hits
        ],
    }


if __name__ == "__main__":
    r = search_filings("AAPL", "why is the company investing in research and development", "FY2024")
    for p in r["passages"]:
        print(f"[{p['score']}] {p['breadcrumb'][:70]}")
        print(f"  {p['text'][:180]}\n")