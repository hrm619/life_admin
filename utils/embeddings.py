from openai import OpenAI

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI()  # reads OPENAI_API_KEY from env
    return _client


def get_embedding(text: str) -> list[float]:
    """Get embedding vector for a single text."""
    client = _get_client()
    response = client.embeddings.create(input=[text], model="text-embedding-3-small")
    return response.data[0].embedding


def get_embeddings(texts: list[str], batch_size: int = 2048) -> list[list[float]]:
    """Get embedding vectors for multiple texts in batches."""
    client = _get_client()
    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.embeddings.create(input=batch, model="text-embedding-3-small")
        # Response may not be in input order — sort by index
        sorted_embs = sorted(response.data, key=lambda x: x.index)
        all_embeddings.extend([e.embedding for e in sorted_embs])
    return all_embeddings
