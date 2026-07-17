FROM python:3.12-slim

RUN apt-get update -qq && apt-get install -y -qq git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app/src

COPY src/ /app/src/
COPY test_p2p_basic.py /app/tests/test_p2p_basic.py
COPY test_aliases_simple.py /app/tests/test_aliases_simple.py
COPY test.sh /tests/test.sh

RUN pip install --no-cache-dir pytest && \
    git init && \
    git config user.email "dev@example.com" && \
    git config user.name "Developer" && \
    git add -A && \
    git commit -m "Baseline commit" && \
    git tag _baseline && \
    chmod +x /tests/test.sh
