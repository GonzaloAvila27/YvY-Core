"""Tests de integración — requieren PostgreSQL migrado a head.

Correr con: docker compose up -d postgres && alembic upgrade head
El CI los ejecuta contra el service pgvector/pg16 (ver .github/workflows/ci.yml).
"""
