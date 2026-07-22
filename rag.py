import os
import pickle
import re
import numpy as np
import httpx

# CJK, Hangul, Cyrillic, Arabic script ranges — presence of these in a reply
# indicates the model leaked a non-English response.
_NON_ENGLISH_SCRIPT = re.compile(
    r"[一-鿿぀-ヿ゠-ヿ가-힯Ѐ-ӿ؀-ۿ]"
)


def has_non_english_script(text: str) -> bool:
    return bool(_NON_ENGLISH_SCRIPT.search(text))

DOCS_DIR = os.getenv("DOCS_DIR", "/app/docs")
INDEX_PATH = os.getenv("INDEX_PATH", "/app/index/index.pkl")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHUNK_SIZE = 600
CHUNK_OVERLAP = 80

status = {
    "state": "idle",  # idle | indexing | ready | error
    "chunks": 0,
    "files": 0,
    "error": None,
}


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


async def embed(text: str, ollama_url: str) -> list[float]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{ollama_url}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]


async def build_index(ollama_url: str):
    status["state"] = "indexing"
    status["error"] = None
    entries = []
    try:
        files = [f for f in os.listdir(DOCS_DIR) if f.endswith(".md")]
        for fname in files:
            path = os.path.join(DOCS_DIR, fname)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            for chunk in chunk_text(text):
                emb = await embed(chunk, ollama_url)
                entries.append({"file": fname, "chunk": chunk, "embedding": emb})

        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "wb") as f:
            pickle.dump(entries, f)

        status["state"] = "ready"
        status["chunks"] = len(entries)
        status["files"] = len(files)
    except Exception as e:
        status["state"] = "error"
        status["error"] = str(e)


def load_index() -> list[dict]:
    if not os.path.exists(INDEX_PATH):
        return []
    with open(INDEX_PATH, "rb") as f:
        return pickle.load(f)


def list_files() -> list[str]:
    """Returns the sorted list of unique source filenames in the index."""
    entries = load_index()
    return sorted({e["file"] for e in entries})


def _cosine(a, b) -> float:
    a, b = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0


async def search(query: str, ollama_url: str, top_k: int = 3) -> list[dict]:
    """Returns top_k chunks with their similarity score included."""
    entries = load_index()
    if not entries:
        return []
    q_emb = await embed(query, ollama_url)
    scored = sorted(
        [{"score": _cosine(q_emb, e["embedding"]), **e} for e in entries],
        key=lambda e: e["score"],
        reverse=True,
    )
    return scored[:top_k]


def init_status():
    """Populate status from persisted index on startup."""
    entries = load_index()
    if entries:
        files = len({e["file"] for e in entries})
        status["state"] = "ready"
        status["chunks"] = len(entries)
        status["files"] = files
