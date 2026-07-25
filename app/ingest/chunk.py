"""Naive but effective text chunker: ~1200 chars with overlap, prefers newlines."""


def chunk_text(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    start, n = 0, len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:  # try not to split mid-line
            nl = text.rfind("\n", start, end)
            if nl > start + size // 2:
                end = nl
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end - overlap if end - overlap > start else end
    return chunks
