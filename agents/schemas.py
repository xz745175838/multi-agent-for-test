"""Pydantic schemas for structured LLM test-case generation."""

from typing import Literal

from pydantic import BaseModel, Field


class TestCase(BaseModel):
    """A single generated HTTP API test case."""

    # 类名以 Test 开头，禁止被 pytest 误收集为测试类
    __test__ = False

    name: str = Field(description="Human-readable test case name")
    method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] = Field(
        description="HTTP method"
    )
    path: str = Field(description="API path, e.g. /api/v1/auth/login")
    headers: dict[str, str] = Field(
        default_factory=lambda: {"Content-Type": "application/json"},
        description="Request headers",
    )
    body: dict | None = Field(default=None, description="JSON request body, if any")
    expected_status: int = Field(
        default=200,
        description="Expected HTTP status code, e.g. 200/400/401",
    )


class TestCaseList(BaseModel):
    """Wrapper forcing the LLM to return a list of TestCase objects."""

    __test__ = False

    cases: list[TestCase] = Field(
        min_length=1,
        description="Non-empty list of generated test cases (JSON key must be exactly 'cases')",
    )
