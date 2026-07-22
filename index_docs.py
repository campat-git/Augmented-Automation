import asyncio
import os
import sys
import rag

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")


async def main():
    files = [f for f in os.listdir(rag.DOCS_DIR) if f.endswith(".md")]
    if not files:
        print(f"No .md files found in {rag.DOCS_DIR}")
        sys.exit(1)

    print(f"Found {len(files)} file(s): {', '.join(files)}")
    print(f"Embedding with model: {rag.EMBED_MODEL}")
    print(f"Ollama: {OLLAMA_URL}")
    print("Indexing... (this may take a few minutes)")

    await rag.build_index(OLLAMA_URL)

    if rag.status["state"] == "ready":
        print(f"\nDone: {rag.status['files']} file(s), {rag.status['chunks']} chunks indexed.")
        print(f"Index saved to: {rag.INDEX_PATH}")
    else:
        print(f"\nError: {rag.status['error']}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
