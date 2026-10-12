FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev
COPY hypertext_search/ hypertext_search/
COPY data/sites.csv data/sites.csv
COPY .deploy/search.sqlite3 data/search.sqlite3
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD [".venv/bin/python", "-m", "hypertext_search", "serve", "--public", "--port", "8080"]
