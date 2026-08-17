"""认证相关接口的集成测试：注册 / 登录 / JWT 保护路由。"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models.user import User


@pytest.mark.integration
async def test_register_success(client: AsyncClient) -> None:
    """POST /api/v1/auth/register：成功注册并返回不含密码的用户信息。"""
    username = f"user_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "secret12"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["username"] == username
    assert "id" in body
    assert "created_at" in body
    assert "hashed_password" not in body
    assert "password" not in body


@pytest.mark.integration
async def test_register_duplicate_username(
    client: AsyncClient,
    test_user: User,
) -> None:
    """同名注册应返回 409 Conflict。"""
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": test_user.username, "password": "secret12"},
    )
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"].lower()


@pytest.mark.integration
async def test_login_returns_access_token(
    client: AsyncClient,
    test_user: User,
) -> None:
    """POST /api/v1/auth/login：校验账号密码并返回 JWT。"""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": test_user.username, "password": "secret12"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert len(body["access_token"]) > 20


@pytest.mark.integration
async def test_login_wrong_password(
    client: AsyncClient,
    test_user: User,
) -> None:
    """错误密码应返回 401。"""
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": test_user.username, "password": "wrong-pass"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_me_unauthorized_without_token(client: AsyncClient) -> None:
    """无 Authorization 访问受保护路由应 401。"""
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


@pytest.mark.integration
async def test_me_with_valid_token(
    client: AsyncClient,
    test_user: User,
    auth_headers: dict[str, str],
) -> None:
    """携带有效 JWT 访问 /me 应 200，并返回当前用户。"""
    response = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == test_user.username
    assert body["id"] == str(test_user.id)
