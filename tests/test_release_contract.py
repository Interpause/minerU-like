"""Annotations must preserve the HTTP contract and share the application version."""

from pathlib import Path

from fastapi.testclient import TestClient

from mineru_like import __version__
from mineru_like.api import create_app
from mineru_like.config import Settings


def test_health_version_and_unmodeled_response_schema(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        assert client.get("/v1/health").json() == {
            "status": "ok",
            "version": __version__,
            "features": {"sources": ["file_id"], "output_formats": ["zip"]},
            "engine_status": "unconfigured",
            "engine_device": None,
        }
        paths = client.get("/openapi.json").json()["paths"]
        for path, method in (("/v1/health", "get"), ("/v1/parse/jobs", "post")):
            status = "202" if method == "post" else "200"
            assert (
                paths[path][method]["responses"][status]["content"]["application/json"]["schema"]
                == {}
            )
        response = client.post("/v1/parse/jobs", json={"files": ["invalid"]})
        assert response.status_code == 422
        error = response.json()["detail"][0]
        assert error["loc"] == ["body", "files", 0]
        assert error["type"] == "dict_type"
