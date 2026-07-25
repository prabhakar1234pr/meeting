"""Chroma vector store holding two kinds of vectors in one collection:

- kind="source"  — chunks of a Knowledge Store source (metadata.source_id)
- kind="meeting" — memory of a past meeting (metadata.agent_id, meeting_id)

Answer-time retrieval filters to an agent's connected source_ids (knowledge)
plus its own past meetings (memory).
"""
import chromadb

from . import config, embeddings

_col = None


def _collection():
    global _col
    if _col is None:
        client = chromadb.PersistentClient(path=config.CHROMA_PATH)
        _col = client.get_or_create_collection(
            "knowledge", metadata={"hnsw:space": "cosine"}
        )
    return _col


def add_source_chunks(source_id: str, chunks: list[str]) -> None:
    if not chunks:
        return
    col = _collection()
    col.add(
        ids=[f"src:{source_id}:{i}" for i in range(len(chunks))],
        documents=chunks,
        embeddings=embeddings.embed(chunks),
        metadatas=[{"kind": "source", "source_id": source_id} for _ in chunks],
    )


def delete_source(source_id: str) -> None:
    try:
        _collection().delete(where={"source_id": source_id})
    except Exception:
        pass


def add_meeting_memory(meeting_id: str, agent_id: str, chunks: list[str]) -> None:
    if not chunks:
        return
    col = _collection()
    col.add(
        ids=[f"mtg:{meeting_id}:{i}" for i in range(len(chunks))],
        documents=chunks,
        embeddings=embeddings.embed(chunks),
        metadatas=[
            {"kind": "meeting", "meeting_id": meeting_id, "agent_id": agent_id}
            for _ in chunks
        ],
    )


def _query(where: dict, query_emb, k: int):
    if k <= 0:
        return []
    res = _collection().query(query_embeddings=[query_emb], n_results=k, where=where)
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    return list(zip(docs, metas))


def retrieve(query: str, source_ids: list[str], agent_id: str, k: int = 6):
    """Return [(document, metadata), ...] from the agent's knowledge + memory.

    Knowledge sources and meeting memory are queried SEPARATELY and then merged,
    so memory (which can be large) never crowds out the connected sources. When
    an agent has sources connected, they get the majority of the slots.
    """
    emb = embeddings.embed_one(query)
    src = _query(
        {"$and": [{"kind": {"$eq": "source"}}, {"source_id": {"$in": source_ids}}]}, emb, k
    ) if source_ids else []
    mem = _query(
        {"$and": [{"kind": {"$eq": "meeting"}}, {"agent_id": {"$eq": agent_id}}]}, emb, k
    ) if agent_id else []

    if not src:
        return mem[:k]
    if not mem:
        return src[:k]
    n_src = max(1, (2 * k) // 3)          # ~2/3 to connected knowledge sources
    return src[:n_src] + mem[: k - n_src]
