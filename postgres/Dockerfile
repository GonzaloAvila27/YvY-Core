FROM postgis/postgis:16-3.4

# pgvector — requerido para knowledge.embedding (Track IA, I0)
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*
