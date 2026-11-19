FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
COPY packages/ig-queue ./packages/ig-queue

COPY packages/ig-core ./packages/ig-core
RUN pip install --no-cache-dir -r requirements.txt ./packages/ig-core

COPY app ./app

RUN mkdir -p /app/output

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
