cd /opt/AA02
docker run --rm -e OLLAMA_URL=http://10.0.0.77:11434  -v ./docs:/app/docs  -v ./index:/app/index  aa02:latest python index_docs.py
