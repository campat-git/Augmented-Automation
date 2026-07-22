import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import httpx
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
import rag

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.45"))
LOG_DIR = os.getenv("LOG_DIR", "/app/logs")
CONTEXT_FILE = os.getenv("CONTEXT_FILE", "/app/context.md")
MODEL_FILE = os.getenv("MODEL_FILE", "/app/model_used")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))
USERS_FILE = os.getenv("USERS_FILE", "/app/users.json")

# token → username for active sessions; cleared on container restart
_sessions: dict[str, str] = {}


def _load_users() -> dict:
    if os.path.isfile(USERS_FILE):
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _verify_password(password: str, stored: str) -> bool:
    salt_b64, key_b64 = stored.split(":")
    salt = base64.b64decode(salt_b64)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return hmac.compare_digest(base64.b64encode(key).decode(), key_b64)


def _get_username(request: Request) -> str:
    return _sessions.get(request.cookies.get("aa_session", ""), "")


_LOGIN_PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login – Augmented Automation</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: system-ui, sans-serif;
      background: #ffffff;
      color: #000000;
      height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .card {{
      border: 1px solid #ddd;
      border-radius: 8px;
      padding: 40px 48px;
      width: 340px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    h1 {{
      font-family: "Bookman Old Style", "URW Bookman", Georgia, serif;
      font-size: 1.1rem;
      font-weight: 600;
      letter-spacing: 0.02em;
    }}
    input[type=text], input[type=password] {{
      width: 100%;
      padding: 8px 10px;
      border: 1px solid #ccc;
      border-radius: 6px;
      font-size: 0.95rem;
      background: #ffffff;
      color: #000000;
    }}
    button {{
      background: #f0f0f0;
      color: #000000;
      border: 1px solid #ccc;
      border-radius: 6px;
      padding: 8px 14px;
      cursor: pointer;
      font-size: 0.9rem;
    }}
    button:hover {{ background: #e0e0e0; }}
    .error {{ color: #c00; font-size: 0.85rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Augmented Automation</h1>
    <form method="post" action="/api/login">
      <div style="display:flex;flex-direction:column;gap:12px">
        <input type="text" name="username" placeholder="Username" autofocus required autocomplete="username">
        <input type="password" name="password" placeholder="Password" required autocomplete="current-password">
        {error}
        <button type="submit">Sign in</button>
      </div>
    </form>
  </div>
</body>
</html>
"""


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not _load_users():
            return await call_next(request)
        if request.url.path in ("/login", "/api/login"):
            return await call_next(request)
        token = request.cookies.get("aa_session", "")
        if token not in _sessions:
            return RedirectResponse(url="/login")
        return await call_next(request)

SYSTEM_CONTEXT = ""
MODEL_NAME = ""

# Meta-questions about the index itself ("what documents do you have") have no
# chunk content to semantically match against, so they're detected separately
# and answered directly from the index rather than via RAG search + the LLM.
_LIST_DOCS_RE = re.compile(
    r"\b(list|show|what|which)\b.{0,40}\b(document|doc|file|source|reference)s?\b.{0,40}"
    r"\b(index(ed)?|have|available|got|there|access)\b",
    re.IGNORECASE | re.DOTALL,
)

# "Who made this app" is likewise a meta-question with no matching chunk
# content, so it's answered directly instead of falling through to the LLM.
_AUTHOR_RE = re.compile(
    r"\bwho\b.{0,40}\b(made|create[ds]?|built|develop(ed|er)?|wrote|author(ed)?|behind)\b"
    r".{0,40}\b(this|the app|application|you|it|program|tool)\b",
    re.IGNORECASE | re.DOTALL,
)

def log_exchange(model: str, user_query: str, response: str, sources: list[str], username: str = ""):
    os.makedirs(LOG_DIR, exist_ok=True)
    now = datetime.now()
    path = os.path.join(LOG_DIR, f"chat-{now.strftime('%Y-%m-%d')}.txt")
    with open(path, "a", encoding="utf-8") as f:
        user_tag = f" user={username}" if username else ""
        f.write(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] model={model}{user_tag}\n")
        f.write(f"User: {user_query}\n")
        f.write(f"Assistant: {response}\n")
        if sources:
            f.write(f"Sources: {', '.join(sources)}\n")
        f.write("\n---\n\n")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global SYSTEM_CONTEXT, MODEL_NAME
    if os.path.isfile(CONTEXT_FILE):
        with open(CONTEXT_FILE, "r", encoding="utf-8") as f:
            SYSTEM_CONTEXT = f.read().strip()
    if os.path.isfile(MODEL_FILE):
        with open(MODEL_FILE, "r", encoding="utf-8") as f:
            MODEL_NAME = f.read().strip()
    if not MODEL_NAME:
        raise RuntimeError(
            f"No model configured — put a model name (e.g. llama3.2:3b) in {MODEL_FILE}"
        )
    rag.init_status()
    yield


app = FastAPI(lifespan=lifespan)
app.add_middleware(_AuthMiddleware)
app.mount("/static", StaticFiles(directory="static"), name="static")


class Message(BaseModel):
    role: str
    content: str


class AttachedFile(BaseModel):
    name: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    attached_files: list[AttachedFile] = []


@app.get("/login", response_class=HTMLResponse)
async def login_page(error: str = ""):
    error_html = '<p class="error">Incorrect username or password.</p>' if error else ""
    return HTMLResponse(_LOGIN_PAGE.format(error=error_html))


@app.post("/api/login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    users = _load_users()
    stored = users.get(username)
    if stored and _verify_password(password, stored):
        token = secrets.token_hex(32)
        _sessions[token] = username
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie("aa_session", token, httponly=True, samesite="strict")
        return response
    return RedirectResponse(url="/login?error=1", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("static/index.html")


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    data = await file.read()
    name = file.filename or "upload"
    try:
        if name.lower().endswith(".pdf"):
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(data))
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif name.lower().endswith(".docx"):
            import docx
            doc = docx.Document(io.BytesIO(data))
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            text = data.decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not extract text from {name}: {e}")
    return {"name": name, "content": text}


@app.get("/api/documents")
async def documents():
    return {"files": rag.list_files()}


@app.get("/api/model")
async def model_info():
    return {"model": MODEL_NAME}


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    username = _get_username(request)
    user_query = req.messages[-1].content if req.messages else ""

    if user_query and _AUTHOR_RE.search(user_query):
        content = "campbell paterson"
        log_exchange(MODEL_NAME, user_query, content, [], username)
        return {"content": content, "sources": []}

    if user_query and _LIST_DOCS_RE.search(user_query):
        files = rag.list_files()
        if files:
            content = "The following documents are indexed:\n\n" + "\n".join(f"- {f}" for f in files)
        else:
            content = "No documents are currently indexed."
        log_exchange(MODEL_NAME, user_query, content, files, username)
        return {"content": content, "sources": files}

    # Retrieve relevant chunks — only use those above the relevance threshold
    sources = []
    if rag.status["state"] == "ready" and user_query:
        chunks = await rag.search(user_query, OLLAMA_URL)
        relevant = [c for c in chunks if c["score"] >= RELEVANCE_THRESHOLD]
    else:
        relevant = []

    # If the index is loaded but nothing relevant was found and no files attached, respond without LLM
    if rag.status["state"] == "ready" and not relevant and not req.attached_files:
        no_match_reply = (
            "I wasn't able to find relevant information in the available documents. "
            "**Try uploading a file** with the relevant details."
        )
        log_exchange(MODEL_NAME, user_query, no_match_reply, [], username)
        return {"content": no_match_reply, "sources": []}

    context_parts = []
    if relevant:
        rag_text = "\n\n---\n\n".join(f"[{c['file']}]\n{c['chunk']}" for c in relevant)
        context_parts.append(f"Indexed reference material:\n\n{rag_text}")
    source_files = {c["file"] for c in relevant}
    sources = list(source_files | {f.name for f in req.attached_files})

    if req.attached_files:
        file_text = "\n\n---\n\n".join(
            f"[Attached: {f.name}]\n{f.content}" for f in req.attached_files
        )
        context_parts.append(f"User-attached files:\n\n{file_text}")

    system_content = (
        "You are a helpful assistant. Always answer in English, regardless of the "
        "language the question is asked in or the language of the reference material.\n\n"
    )
    if SYSTEM_CONTEXT:
        system_content += (
            "Standing instructions (apply to every answer, and take precedence over "
            "the reference material below if the two conflict):\n\n"
            + SYSTEM_CONTEXT + "\n\n"
        )
    if context_parts:
        system_content += (
            "Answer the user's question using ONLY the reference material provided "
            "below, except where it conflicts with the standing instructions above, "
            "in which case the standing instructions win. Do not use any other "
            "outside knowledge. If the reference material does not contain enough "
            "information to answer fully, say so in English and ask the user for "
            "clarification.\n\n"
            + "\n\n===\n\n".join(context_parts)
        )
    messages = [{"role": "system", "content": system_content}]
    messages.extend([m.model_dump() for m in req.messages])
    # Reinforce right before generation — models can drift from an earlier
    # system message over a long conversation.
    messages.append({"role": "system", "content": "Reminder: respond in English only."})

    payload = {"model": MODEL_NAME, "messages": messages, "stream": False}

    async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
        try:
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]

            if rag.has_non_english_script(content):
                retry_messages = messages + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not in English. "
                            "Rewrite it in English only, with no other language."
                        ),
                    },
                ]
                retry_resp = await client.post(
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": MODEL_NAME, "messages": retry_messages, "stream": False},
                )
                retry_resp.raise_for_status()
                retry_content = retry_resp.json()["message"]["content"]
                if not rag.has_non_english_script(retry_content):
                    content = retry_content

            log_exchange(MODEL_NAME, user_query, content, sources, username)
            return {"content": content, "sources": sources}
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Ollama error: {e}")


