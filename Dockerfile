FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y build-essential ca-certificates && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# Default: run the web dashboard
# Use the shell form of CMD so $PORT environment variable is evaluated
CMD uvicorn ui_server:app --host 0.0.0.0 --port ${PORT:-8000}
