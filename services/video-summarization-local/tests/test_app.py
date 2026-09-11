import httpx
import pytest

from app import app


@pytest.mark.asyncio
async def test_health_and_model_contract() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/v1/ready")).status_code == 200
        payload = (await client.get("/v1/models")).json()
        assert payload["object"] == "list"
        assert payload["data"][0]["object"] == "model"


@pytest.mark.asyncio
async def test_unsupported_livestream_is_explicit() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/generate_captions", json={})
        assert response.status_code == 501
