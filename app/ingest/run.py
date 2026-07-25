"""Run the ingestion pipeline for one source (blocking; run in a thread).

fetch → chunk → embed → Chroma, updating the source status as it goes.
"""
from .. import db, vectorstore
from . import fetch
from .chunk import chunk_text


def ingest_source(source_id: str) -> None:
    src = db.get_source(source_id)
    if not src:
        return
    db.set_source_status(source_id, "ingesting")
    try:
        text = fetch.fetch_source(src["type"], src["uri"])
        chunks = chunk_text(text)
        if not chunks:
            db.set_source_status(source_id, "error", chunk_count=0, error="no text extracted")
            return
        vectorstore.delete_source(source_id)  # idempotent re-ingest
        vectorstore.add_source_chunks(source_id, chunks)
        db.set_source_status(source_id, "ready", chunk_count=len(chunks), error=None)
    except Exception as e:  # noqa: BLE001 — surface any failure to the UI
        db.set_source_status(source_id, "error", error=str(e)[:500])
