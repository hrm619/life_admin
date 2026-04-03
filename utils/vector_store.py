import chromadb

from utils.embeddings import get_embedding, get_embeddings


def create_index(chunks: list[dict]) -> chromadb.Collection:
    """Create an in-memory vector index from chunks.

    Each chunk: {"id": str, "text": str, "metadata": {"source": str, ...}}
    """
    client = chromadb.EphemeralClient()
    collection = client.create_collection(
        name="life_admin_session",
        metadata={"hnsw:space": "cosine"},
    )

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [c["metadata"] for c in chunks]

    embeddings = get_embeddings(texts)

    collection.add(
        ids=ids,
        embeddings=embeddings,
        documents=texts,
        metadatas=metadatas,
    )
    return collection


def search_index(
    collection: chromadb.Collection,
    query: str,
    n_results: int = 10,
    where: dict | None = None,
) -> list[dict]:
    """Search the vector index with semantic + keyword matching.

    Runs both a semantic search (embeddings) and a keyword search (where_document),
    then merges and deduplicates results for better recall.
    """
    query_embedding = get_embedding(query)

    # Semantic search
    sem_kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
    }
    if where:
        sem_kwargs["where"] = where
    sem_results = collection.query(**sem_kwargs)

    # Keyword search — extract key terms from query for text matching
    # Use the full query as a substring match via where_document
    kw_results = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    keywords = [w for w in query.split() if len(w) > 2]
    if keywords:
        for keyword in keywords:
            try:
                kw_kwargs = {
                    "query_embeddings": [query_embedding],
                    "n_results": n_results,
                    "where_document": {"$contains": keyword},
                }
                if where:
                    kw_kwargs["where"] = where
                kw_res = collection.query(**kw_kwargs)
                # Merge into kw_results
                kw_results["ids"][0].extend(kw_res["ids"][0])
                kw_results["documents"][0].extend(kw_res["documents"][0])
                kw_results["metadatas"][0].extend(kw_res["metadatas"][0])
                kw_results["distances"][0].extend(kw_res["distances"][0])
            except Exception:
                pass  # keyword search is best-effort

    # Merge and deduplicate, preferring lower distance
    seen = {}
    for results_set in [sem_results, kw_results]:
        for i in range(len(results_set["ids"][0])):
            doc_id = results_set["ids"][0][i]
            distance = results_set["distances"][0][i]
            if doc_id not in seen or distance < seen[doc_id]["distance"]:
                seen[doc_id] = {
                    "text": results_set["documents"][0][i],
                    "metadata": results_set["metadatas"][0][i],
                    "distance": distance,
                }

    # Sort by distance and return top n_results
    output = sorted(seen.values(), key=lambda x: x["distance"])[:n_results]
    return output
