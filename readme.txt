AA02 - Local LLM Web Interface with RAG
========================================

A web-based chat interface for Ollama with a RAG (Retrieval-Augmented Generation)
repository. Drop markdown files into the docs folder, run the indexer once, then
start the chat server. The chat will only answer from the indexed documents.

PROJECT STRUCTURE
-----------------
  Dockerfile          - Container build definition
  docker-compose.yml  - Compose config with Ollama URL and volume mounts
  .dockerignore       - Files excluded from Docker build context
  requirements.txt    - Python dependencies (FastAPI, uvicorn, httpx, numpy)
  main.py             - FastAPI backend server
  rag.py              - RAG logic (chunking, embedding, search)
  index_docs.py       - Standalone indexing script (run once before starting server)
  static/index.html   - Web UI (single page, no build step)
  docs/               - Drop .md files here before indexing
  index/              - Persisted embeddings index (survives container restarts)
  logs/               - Server-side chat history, one .txt file per day


FEATURES
--------
  - Model selector (auto-populated from Ollama at startup)
  - RAG answers restricted to indexed documents only
  - If no relevant content is found, asks the user for more detail
  - Source filenames shown under each assistant reply
  - Multi-turn chat history (per session)
  - Save conversation to a timestamped .txt file
  - Every question/answer is also logged server-side to logs/ automatically
  - Clear chat button
  - About popup with version and support info


REQUIREMENTS
------------
  - Docker installed on the target host
  - Ollama running and accessible (default: http://10.0.0.77:11434)
  - nomic-embed-text model pulled in Ollama:
      ollama pull nomic-embed-text   (run this on the Ollama host)


BUILD & DEPLOY (offline transfer)
----------------------------------

  1. Copy the logo from MYCHAT into the static folder:

       cp /opt/TRANS07/MYCHAT/static/J7.png /opt/AA02/static/J7.png

  2. On the internet-connected machine, build the image:

       docker build -t aa02:latest .

  3. Save the image to a tarball:

       docker save aa02:latest | gzip > aa02.tar.gz

  4. Copy aa02.tar.gz to the offline host (USB drive, scp, etc.)

  5. On the offline host, load the image:

       docker load < aa02.tar.gz


INDEXING DOCUMENTS (run once before starting the server)
---------------------------------------------------------

  1. Place .md files into the docs/ folder on the host machine

  2. Run the indexer (this calls Ollama to generate embeddings):

       docker run --rm \
         -e OLLAMA_URL=http://10.0.0.77:11434 \
         -v $(pwd)/docs:/app/docs \
         -v $(pwd)/index:/app/index \
         --add-host host.docker.internal:host-gateway \
         aa02:latest python index_docs.py

  3. The indexer will print progress and confirm when done:

       Found 3 file(s): guide.md, policy.md, notes.md
       Indexing... (this may take a few minutes)
       Done: 3 file(s), 47 chunks indexed.

  Re-run the indexer any time you add or change files in docs/,
  then restart the chat container to pick up the new index.


STARTING THE CHAT SERVER
-------------------------

       docker run -d --name aa02 \
         -p 8081:8080 \
         -e OLLAMA_URL=http://10.0.0.77:11434 \
         -v $(pwd)/docs:/app/docs \
         -v $(pwd)/index:/app/index \
         -v $(pwd)/logs:/app/logs \
         --add-host host.docker.internal:host-gateway \
         aa02:latest

  Open a browser and go to:

       http://localhost:8081


HOW RAG WORKS
-------------
  - Each message is embedded and compared against the indexed document chunks
  - Only chunks above the relevance threshold (default 0.45) are used
  - The model is instructed to answer ONLY from those chunks
  - If nothing relevant is found, the user is asked to rephrase or provide
    more detail — the model will not fall back to general knowledge
  - Sources cited below each reply show which documents were referenced

  The relevance threshold can be tuned via environment variable:
    -e RELEVANCE_THRESHOLD=0.50   (stricter — fewer but more confident matches)
    -e RELEVANCE_THRESHOLD=0.35   (more lenient — broader matches)


CHAT LOGGING
------------
  Every question and answer is appended server-side to a plain-text log,
  independent of the browser's Save button. Logs are written to /app/logs
  inside the container — mount that path to a host folder (see the docker
  run command above) so logs survive container restarts.

  One file per calendar day: logs/chat-YYYY-MM-DD.txt
  Each entry looks like:

    [2026-07-11 14:32:07] model=qwen2.5
    User: <question>
    Assistant: <answer>
    Sources: file1.md, file2.md

    ---

  The log directory can be relocated via environment variable:
    -e LOG_DIR=/app/logs   (default)


PDF SOURCE SERVER (httpd)
--------------------------
  Each "Sources:" link in the chat UI points to a PDF served by a separate
  Apache httpd container (my-httpd) running on the same host as aa02,
  on port 80. The link is built in the browser as:

       http://<hostname>/<source-file-basename>.pdf

  where <source-file-basename> matches the indexed document's filename
  with the extension swapped to .pdf (e.g. JLVC.md -> JLVC.pdf), and
  <hostname> is whatever hostname/IP is used to reach the chat UI.

  BUILD & TRANSFER (offline transfer, same pattern as aa02)

  1. On the internet-connected machine, save the httpd image:

       docker save httpd:latest | gzip > httpd.tar.gz

  2. Copy httpd.tar.gz to the offline host, along with the PDF files
     (the ones matching your indexed documents, e.g. JLVC.pdf)

  3. On the offline host, load the image:

       docker load < httpd.tar.gz

  STARTING THE HTTPD SERVER

  4. Place the PDF files into a folder on the host, e.g. /opt/TRANS09/DOCS
     (filenames must match the indexed document basenames, with a .pdf
     extension)

  5. Start the container:

       docker run -d --name my-httpd \
         -p 80:80 \
         -v /opt/TRANS09/DOCS:/usr/local/apache2/htdocs \
         httpd:latest

  6. Verify it's serving files by browsing to:

       http://localhost/<file>.pdf

  Note: port 80 must be free on the host. If it's in use, change the
  host-side port (e.g. -p 8080:80) — but the Sources: links in the chat
  UI assume port 80, so you would also need to update the frontend
  (static/index.html, sourcePdfUrl function) to include the new port.



docker load -i markitdown-latest-amd64.tar.gz
docker run --rm -i markitdown:latest < yourfile.pdf > output.md


docker run -d --restart unless-stopped --name my-httpd -p 80:80   -v /opt/TRANS09/DOCS:/usr/local/apache2/htdocs  httpd:latest

docker run --rm  -e OLLAMA_URL=http://10.0.0.77:11434  -v  /opt/AA02/docs:/app/docs -v /opt/AA02/index:/app/index --add-host host.docker.internal:host-gateway aa02:latest python index_docs.py
docker run -d --name aa02 -p 8081:8080 -e OLLAMA_URL=http://10.0.0.77:11434  -v  /opt/AA02/docs:/app/docs -v /opt/AA02/index:/app/index -v /opt/AA02/logs:/app/logs  --add-host host.docker.internal:host-gateway aa02:latest
docker run -d --name aa02 -p 8081:8080 -e OLLAMA_URL=http://10.0.0.77:11434 -v /opt/AA02/docs:/app/docs -v /opt/AA02/index:/app/index -v /opt/AA02/logs:/app/logs --add-host host.docker.internal:host-gateway aa02:latest

USAGE
-----
  - Select a model from the dropdown (top right)
  - Type a message and press Enter to send (Shift+Enter for newline)
  - Sources cited below each reply show which docs were used
  - Use Save to download the conversation as a timestamped .txt file
  - Use Clear to reset the conversation history
