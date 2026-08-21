"""LangGraph 图节点单元测试：LLM Mock、条件路由、OpenAPI 解析。
禁止调用真实 OpenAI / DeepSeek：全部通过 mocker 拦截 ChatOpenAI。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from pytest_mock import MockerFixture

from agents.openapi_agent import (
    MAX_RETRIES,
    AgentState,
    generate_cases_node,
    parse_spec_node,
    should_retry,
)
from agents.schemas import TestCase, TestCaseList


def _empty_state(**overrides: Any) -> AgentState:
    """构造一份最小合法 AgentState，便于单测按需覆盖字段。"""
    state: AgentState = {
        "openapi_spec": "",
        "parsed_spec": {},
        "test_cases": [],
        "error_logs": [],
        "retry_count": 0,
        "is_valid": False,
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _sample_parsed_spec() -> dict[str, Any]:
    """生成节点入参用的已解析 OpenAPI 片段。"""
    return {
        "openapi": "3.0.3",
        "info": {"title": "Demo", "version": "1.0.0"},
        "paths": {
            "/health": {
                "get": {
                    "operationId": "healthCheck",
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }


def _valid_case_list() -> TestCaseList:
    """合法的结构化输出（Pydantic），对应 LLM 正常回包。"""
    return TestCaseList(
        cases=[
            TestCase(
                name="test_health_ok",
                method="GET",
                path="/health",
                headers={"Accept": "application/json"},
                body=None,
                expected_status=200,
            )
        ]
    )


def _patch_chat_openai(
    mocker: MockerFixture,
) -> MagicMock:
    """拦截 ChatOpenAI 构造，并注入假 API Key，确保不打真实网关。"""
    mocker.patch("agents.openapi_agent.settings.openai_api_key", "sk-test-not-real")
    mock_llm = MagicMock()
    # 防御：节点若误走 ainvoke，立刻失败而不是打真实 API
    mock_llm.ainvoke = AsyncMock(side_effect=AssertionError("禁止调用真实 LLM ainvoke"))
    mocker.patch("agents.openapi_agent.ChatOpenAI", return_value=mock_llm)
    return mock_llm


# ---------------------------------------------------------------------------
# 测试项 1：generate_cases_node + LLM 结构化输出 Mock
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_cases_node_happy_path(mocker: MockerFixture) -> None:
    """场景 A：Mock LLM 返回合法 TestCaseList，状态应写入 test_cases。"""
    mock_llm = _patch_chat_openai(mocker)
    structured = MagicMock()
    structured.invoke.return_value = _valid_case_list()
    # 当前节点走 sync invoke + with_structured_output（非 ainvoke）
    mock_llm.with_structured_output.return_value = structured

    patch = generate_cases_node(_empty_state(parsed_spec=_sample_parsed_spec()))

    assert patch.get("error_logs") is None
    cases = patch["test_cases"]
    assert len(cases) == 1
    assert cases[0]["name"] == "test_health_ok"
    assert cases[0]["method"] == "GET"
    assert cases[0]["path"] == "/health"
    # 校验走了 with_structured_output（Pydantic 结构化输出），而不是裸 chat
    mock_llm.with_structured_output.assert_called_once()
    # 校验结构化链被同步 invoke 恰好一次（happy path，不重试）
    structured.invoke.assert_called_once()
    # 校验未走 ainvoke，避免测试误打真实 LLM
    mock_llm.ainvoke.assert_not_called()


@pytest.mark.unit
def test_generate_cases_node_output_parser_exception(mocker: MockerFixture) -> None:
    """场景 B：Function Calling 与 JSON Mode 均抛 OutputParserException，写入 error_logs。"""
    mock_llm = _patch_chat_openai(mocker)
    structured = MagicMock()
    malformed = OutputParserException("Malformed JSON")
    structured.invoke.side_effect = malformed
    mock_llm.with_structured_output.return_value = structured

    fallback = MagicMock()
    fallback.invoke.side_effect = malformed
    mock_llm.bind.return_value = fallback

    patch = generate_cases_node(_empty_state(parsed_spec=_sample_parsed_spec()))

    assert patch["test_cases"] == []
    assert patch["is_valid"] is False
    assert patch["error_logs"]
    assert "Malformed JSON" in patch["error_logs"][0]
    assert "LLM structured generation failed" in patch["error_logs"][0]


@pytest.mark.unit
@pytest.mark.parametrize(
    "primary_exc,fallback_content,expect_substr",
    [
        (
            TimeoutError("LLM request timed out"),
            None,
            "timed out",
        ),
        (
            OutputParserException("first path failed"),
            "",
            "LLM structured generation failed",
        ),
        (
            OutputParserException("first path failed"),
            "{}",
            "LLM structured generation failed",
        ),
    ],
    ids=["timeout", "empty_body", "empty_json_object"],
)
def test_generate_cases_node_timeout_or_empty(
    mocker: MockerFixture,
    primary_exc: BaseException,
    fallback_content: str | None,
    expect_substr: str,
) -> None:
    """场景 C：超时，或 fallback 空包 / 空 JSON，节点吞异常并记入 error_logs。"""
    mock_llm = _patch_chat_openai(mocker)
    structured = MagicMock()
    structured.invoke.side_effect = primary_exc
    mock_llm.with_structured_output.return_value = structured

    fallback = MagicMock()
    if fallback_content is None:
        fallback.invoke.side_effect = primary_exc
    else:
        fallback.invoke.return_value = AIMessage(content=fallback_content)
    mock_llm.bind.return_value = fallback

    patch = generate_cases_node(_empty_state(parsed_spec=_sample_parsed_spec()))

    assert patch["test_cases"] == []
    assert patch["is_valid"] is False
    assert any(expect_substr in msg for msg in patch["error_logs"])


# ---------------------------------------------------------------------------
# 测试项 2：should_retry 条件路由参数化
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "state_overrides,expected_route",
    [
        ({"is_valid": True, "retry_count": 0}, "END"),
        ({"is_valid": True, "retry_count": 99}, "END"),
        ({"is_valid": False, "retry_count": 0}, "generate_cases"),
        ({"is_valid": False, "retry_count": MAX_RETRIES - 1}, "generate_cases"),
        ({"is_valid": False, "retry_count": MAX_RETRIES}, "END"),
        ({"is_valid": False, "retry_count": MAX_RETRIES + 2}, "END"),
    ],
    ids=[
        "valid_first_pass",
        "valid_ignores_retry_count",
        "invalid_can_retry",
        "invalid_last_retry_slot",
        "invalid_at_limit",
        "invalid_over_limit",
    ],
)
def test_should_retry_routes(
    state_overrides: dict[str, Any],
    expected_route: str,
) -> None:
    """按不同 AgentState 验证条件边：通过则 END，失败且未达上限则回到 generate_cases。"""
    route = should_retry(_empty_state(**state_overrides))
    assert route == expected_route


# ---------------------------------------------------------------------------
# 测试项 3：parse_spec_node 解析多种 OpenAPI 文本
# ---------------------------------------------------------------------------

_VALID_OPENAPI: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "Health API", "version": "1.0.0"},
    "paths": {
        "/health": {
            "get": {
                "operationId": "healthCheck",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

_MISSING_PATHS: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "No Paths", "version": "1.0.0"},
}

_JWT_AUTH: dict[str, Any] = {
    "openapi": "3.0.3",
    "info": {"title": "JWT API", "version": "1.0.0"},
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            }
        }
    },
    "security": [{"bearerAuth": []}],
    "paths": {
        "/api/v1/auth/me": {
            "get": {
                "security": [{"bearerAuth": []}],
                "responses": {"200": {"description": "current user"}},
            }
        }
    },
}


@pytest.mark.unit
@pytest.mark.parametrize(
    "spec_text,expect_ok,expect_paths,expect_error_substr",
    [
        (json.dumps(_VALID_OPENAPI), True, ["/health"], None),
        (yaml.safe_dump(_MISSING_PATHS, sort_keys=False), True, [], None),
        (json.dumps(_JWT_AUTH), True, ["/api/v1/auth/me"], None),
        ('{"openapi": "3.0.3", "info":', False, None, "Failed to parse openapi_spec"),
        ("", False, None, "openapi_spec is empty"),
    ],
    ids=[
        "valid_json_schema",
        "yaml_missing_paths",
        "json_jwt_bearer_auth",
        "malformed_json",
        "empty_spec",
    ],
)
def test_parse_spec_node_variants(
    spec_text: str,
    expect_ok: bool,
    expect_paths: list[str] | None,
    expect_error_substr: str | None,
) -> None:
    """解析节点：合法 Schema 抽出 paths；非法/空文本写入 error_logs，不向外抛异常。"""
    patch = parse_spec_node(_empty_state(openapi_spec=spec_text))

    if expect_ok:
        parsed = patch["parsed_spec"]
        assert isinstance(parsed, dict)
        paths = parsed.get("paths") or {}
        assert sorted(paths.keys()) == sorted(expect_paths or [])
        if "bearerAuth" in spec_text:
            schemes = parsed.get("components", {}).get("securitySchemes", {})
            assert schemes["bearerAuth"]["bearerFormat"] == "JWT"
        assert "error_logs" not in patch
    else:
        assert "parsed_spec" not in patch
        assert patch["error_logs"]
        assert expect_error_substr is not None
        assert any(expect_error_substr in msg for msg in patch["error_logs"])
