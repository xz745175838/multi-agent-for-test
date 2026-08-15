"""LangGraph OpenAPI agent: parse specs, LLM-generate and validate test cases."""

from __future__ import annotations

import json
import operator
from typing import Annotated, TypedDict

import yaml
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from agents.schemas import TestCaseList
from app.core.config import settings

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
MAX_RETRIES = 3


class AgentState(TypedDict):
    """Global state shared across OpenAPI agent graph nodes."""

    openapi_spec: str
    parsed_spec: dict
    # Intentionally no reducer: each retry regenerates and replaces test_cases
    # (unlike error_logs, which accumulate). Appending would mix invalid prior runs.
    test_cases: list[dict]
    error_logs: Annotated[list[str], operator.add]
    retry_count: int
    is_valid: bool


def _extract_required_operations(parsed_spec: dict) -> set[tuple[str, str]]:
    """Return {(path, METHOD)} pairs defined in the OpenAPI paths object."""
    required: set[tuple[str, str]] = set()
    paths = parsed_spec.get("paths") or {}
    if not isinstance(paths, dict):
        return required
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            method_upper = method.upper()
            if method_upper not in HTTP_METHODS:
                continue
            if method.startswith("x-") or not isinstance(operation, dict):
                continue
            required.add((path, method_upper))
    return required


def parse_spec_node(state: AgentState) -> dict:
    """Parse raw OpenAPI YAML/JSON text into a structured dict."""
    raw = (state.get("openapi_spec") or "").strip()
    if not raw:
        return {"error_logs": ["openapi_spec is empty; nothing to parse."]}

    try:
        if raw.startswith("{") or raw.startswith("["):
            parsed_data = json.loads(raw)
        else:
            parsed_data = yaml.safe_load(raw)

        if not isinstance(parsed_data, dict):
            return {
                "error_logs": [
                    f"Parsed OpenAPI root must be a mapping/object, "
                    f"got {type(parsed_data).__name__}."
                ]
            }
        return {"parsed_spec": parsed_data}
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        return {"error_logs": [f"Failed to parse openapi_spec: {exc}"]}
    except Exception as exc:  # noqa: BLE001 — surface unexpected parse errors into state
        return {"error_logs": [f"Unexpected error while parsing openapi_spec: {exc}"]}


def _coerce_test_case_list(payload: object) -> TestCaseList:
    """Normalize common LLM JSON shapes into TestCaseList.

    DeepSeek json_mode often returns ``{"test_cases": [...]}`` or a bare list
    instead of ``{"cases": [...]}``; accept those and re-validate.

    Bare list: a top-level JSON array ``[{...}, {...}]`` is treated as the
    ``cases`` value directly — wrapped to ``{"cases": payload}`` then validated.
    """
    if isinstance(payload, TestCaseList):
        return payload
    if isinstance(payload, list):
        # Bare test-case list → wrap under the required ``cases`` key.
        return TestCaseList.model_validate({"cases": payload})
    if isinstance(payload, dict):
        data = dict(payload)
        if "cases" not in data:
            if "test_cases" in data:
                data["cases"] = data.pop("test_cases")
            elif "items" in data:
                data["cases"] = data.pop("items")
        return TestCaseList.model_validate(data)
    raise TypeError(f"Unexpected structured payload type: {type(payload).__name__}")

def generate_cases_node(state: AgentState) -> dict:
    """Call an OpenAI-compatible LLM with structured output to generate test cases."""
    parsed = state.get("parsed_spec") or {}
    if not parsed:
        hint = state.get("error_logs") or ["No parsed_spec available."]
        return {
            "test_cases": [],
            "is_valid": False,
            "error_logs": [
                "generate_cases skipped: parsed_spec is empty. "
                f"Prior errors: {'; '.join(hint)}"
            ],
        }

    if not settings.openai_api_key:
        return {
            "test_cases": [],
            "is_valid": False,
            "error_logs": [
                "OPENAI_API_KEY is empty; configure it in .env before generating cases."
            ],
        }

    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_api_base,
        temperature=0.2,
    )

    prior_errors = state.get("error_logs") or []
    retry_count = state.get("retry_count") or 0
    feedback = ""
    if retry_count > 0 and prior_errors:
        feedback = (
            "\n\n## Previous validation feedback (fix and fix)\n"
            + "\n".join(f"- {e}" for e in prior_errors[-10:])
        )

    schema_json = json.dumps(TestCaseList.model_json_schema(), ensure_ascii=False, indent=2)
    system_prompt = (
        "You are a senior API test engineer. "
        "Given an OpenAPI document (as JSON), generate concrete HTTP test cases.\n"
        "Think step by step (Chain-of-Thought):\n"
        "1) List every path and HTTP method in `paths`.\n"
        "2) For each operation, invent a realistic case name, headers, optional body, "
        "and expected_status based on the declared responses.\n"
        "3) Ensure coverage: every path+method pair must appear in at least one case.\n"
        "4) Prefer JSON Content-Type headers for POST/PUT/PATCH with bodies.\n"
        "You MUST reply with a single JSON object only (no markdown fences).\n"
        'The top-level key MUST be exactly "cases" (a non-empty array).\n'
        f"JSON Schema:\n{schema_json}"
    )
    human_prompt = (
        f"Retry attempt: {retry_count}\n"
        f"OpenAPI parsed_spec JSON:\n```json\n"
        f"{json.dumps(parsed, ensure_ascii=False, indent=2)}\n```"
        f"{feedback}"
    )
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt),
    ]

    try:
        # Prefer tool/function calling when the gateway supports it; fall back to
        # json_object mode + explicit schema (DeepSeek-friendly).
        result: TestCaseList | None = None
        try:
            # Prefer function_calling: LangChain binds TestCaseList as a tool/function
            # schema so the model returns a typed object (not free-form text). If the
            # gateway (e.g. some DeepSeek proxies) rejects tools, the except below
            # falls back to json_object + the schema already embedded in system_prompt.
            structured = llm.with_structured_output(TestCaseList, method="function_calling")
            raw_structured = structured.invoke(messages)
            result = _coerce_test_case_list(raw_structured)
        except Exception:
            json_llm = llm.bind(response_format={"type": "json_object"})
            ai_message = json_llm.invoke(messages)
            content = ai_message.content
            if isinstance(content, list):
                # Some SDKs return content blocks; keep text parts only.
                content = "".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            text = str(content).strip()
            if text.startswith("```"):
                text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            payload = json.loads(text)
            result = _coerce_test_case_list(payload)

        cases = [case.model_dump() for case in result.cases]
        if not cases:
            return {
                "test_cases": [],
                "is_valid": False,
                "error_logs": [
                    "LLM returned an empty cases list; expected non-empty "
                    'JSON like {"cases":[...]}.'
                ],
            }
        return {"test_cases": cases}
    except Exception as exc:  # noqa: BLE001 — keep graph alive; validate/retry will handle
        return {
            "test_cases": [],
            "is_valid": False,
            "error_logs": [f"LLM structured generation failed: {exc}"],
        }


def validate_cases_node(state: AgentState) -> dict:
    """Validate generated cases cover required OpenAPI path+method pairs."""
    cases = state.get("test_cases") or []
    parsed = state.get("parsed_spec") or {}
    retry_count = int(state.get("retry_count") or 0)
    errors: list[str] = []

    if not cases:
        errors.append("test_cases is empty.")

    required = _extract_required_operations(parsed)
    covered: set[tuple[str, str]] = set()
    for case in cases:
        if not isinstance(case, dict):
            continue
        path = case.get("path")
        method = (case.get("method") or "").upper()
        if isinstance(path, str) and method:
            covered.add((path, method))

    missing = sorted(required - covered)
    for path, method in missing:
        errors.append(f"缺失对 {method} {path} 的测试")

    if not errors:
        return {"is_valid": True}

    new_retry = retry_count + 1
    if new_retry >= MAX_RETRIES:
        errors.append(f"重试次数上限（{MAX_RETRIES}），停止自我纠错。")

    return {
        "is_valid": False,
        "retry_count": new_retry,
        "error_logs": errors,
    }


def should_retry(state: AgentState) -> str:
    """Route after validation: END if valid or retries exhausted, else regenerate."""
    if state.get("is_valid"):
        return "END"
    if int(state.get("retry_count") or 0) < MAX_RETRIES:
        return "generate_cases"
    return "END"


workflow = StateGraph(AgentState)
workflow.add_node("parse_spec", parse_spec_node)
workflow.add_node("generate_cases", generate_cases_node)
workflow.add_node("validate_cases", validate_cases_node)
workflow.set_entry_point("parse_spec")
workflow.add_edge("parse_spec", "generate_cases")
workflow.add_edge("generate_cases", "validate_cases")
workflow.add_conditional_edges(
    "validate_cases",
    should_retry,
    {
        "generate_cases": "generate_cases",
        "END": END,
    },
)
app = workflow.compile()


if __name__ == "__main__":
    sample_openapi_yaml = """
openapi: 3.0.3
info:
  title: Sample Health API
  version: 1.0.0
paths:
  /health:
    get:
      operationId: healthCheck
      summary: Liveness probe
      responses:
        "200":
          description: OK
  /api/v1/auth/login:
    post:
      operationId: login
      summary: User login
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                username:
                  type: string
                password:
                  type: string
      responses:
        "200":
          description: JWT issued
        "401":
          description: Unauthorized
"""

    initial_state: AgentState = {
        "openapi_spec": sample_openapi_yaml,
        "parsed_spec": {},
        "test_cases": [],
        "error_logs": [],
        "retry_count": 0,
        "is_valid": False,
    }
    result = app.invoke(initial_state)
    print("is_valid:", result.get("is_valid"))
    print("retry_count:", result.get("retry_count"))
    print("test_cases:", json.dumps(result.get("test_cases"), ensure_ascii=False, indent=2))
    print("error_logs:", result.get("error_logs"))
