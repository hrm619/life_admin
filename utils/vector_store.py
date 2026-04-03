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
    n_results: int = 5,
    where: dict | None = None,
) -> list[dict]:
    """Search the vector index. Returns list of {"text", "metadata", "distance"}."""
    query_embedding = get_embedding(query)

    kwargs = {
        "query_embeddings": [query_embedding],
        "n_results": n_results,
    }
    if where:
        kwargs["where"] = where

    results = collection.query(**kwargs)

    output = []
    for i in range(len(results["ids"][0])):
        output.append(
            {
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            }
        )
    return output
