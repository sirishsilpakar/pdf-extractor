"""API integration tests — upload endpoint and hash check"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from db.repository import DatabaseRepository


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with isolated DB and upload dir"""
    db_file = str(tmp_path / "test.db")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    monkeypatch.setenv("EXTRACTOR_DB_PATH", db_file)
    monkeypatch.setattr("config.UPLOAD_DIR", upload_dir)

    # Isolate the lru_cache so each test gets a fresh DB
    from api.v1 import deps

    deps.get_db.cache_clear()

    app = create_app()
    with TestClient(app) as c:
        yield c


def _make_pdf_bytes(content: bytes = b"fake PDF content") -> bytes:
    return b"%PDF-1.4\n" + content


def test_upload_single_file(client):
    data = _make_pdf_bytes(b"hello world")
    resp = client.post(
        "/api/v1/upload",
        files=[("files", ("test.pdf", io.BytesIO(data), "application/pdf"))],
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "test.pdf"
    assert len(body[0]["content_hash"]) == 64
    assert body[0]["file_id"]


def test_upload_rejects_non_pdf(client):
    resp = client.post(
        "/api/v1/upload",
        files=[("files", ("test.txt", io.BytesIO(b"not a pdf"), "text/plain"))],
    )
    assert resp.status_code == 400


def test_check_hashes_empty(client):
    resp = client.post("/api/v1/upload/check-hashes", json={"hashes": ["abc123"]})
    assert resp.status_code == 200
    body = resp.json()
    assert "abc123" in body["unprocessed"]
    assert "abc123" not in body["already_processed"]


def test_check_hashes_finds_uploaded(tmp_path, monkeypatch):
    """After saving a record with a known hash, check-hashes should find it.

    We use app.dependency_overrides to swap the DB dependency, the correct
    FastAPI pattern that works regardless of lru_cache on the provider
    """
    # Create an isolated DB with a known record
    db = DatabaseRepository(str(tmp_path / "test.db"))
    db.init_schema()
    db.save_extracted_text(
        source_path="/a.pdf",
        filename="a.pdf",
        rel_path="a.pdf",
        txt_path="/out/a.txt",
        method="direct",
        char_count=100,
        page_count=1,
        content_hash="known_hash_42",
    )

    # Patch via dependency_overrides the only reliable way with lru_cache deps
    from api.v1.deps import get_db

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db

    with TestClient(app) as c:
        resp = c.post(
            "/api/v1/upload/check-hashes",
            json={"hashes": ["known_hash_42", "unknown"]},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "known_hash_42" in body["already_processed"]
    assert "unknown" in body["unprocessed"]
