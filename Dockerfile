FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py rag.py index_docs.py manage_users.py ./
COPY static/ static/

RUN mkdir -p /app/docs /app/index /app/logs
RUN touch /app/context.md
RUN touch /app/model_used

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
