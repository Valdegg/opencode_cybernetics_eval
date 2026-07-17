FROM python:3.12-slim

RUN apt-get update -qq && apt-get install -y -qq git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app/src

COPY src/ /app/src/
COPY tests/test_p2p_basic.py /app/tests/test_p2p_basic.py

RUN pip install --no-cache-dir pytest && \
    echo "__pycache__/" > /app/.gitignore && \
    echo "*.pyc" >> /app/.gitignore && \
    git init && \
    git config user.email "dev@example.com" && \
    git config user.name "Developer" && \
    git add -A && \
    git commit -m "Initial commit" && \
    git tag _baseline

CMD ["/bin/bash"]
