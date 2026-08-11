"""LangGraph OpenAPI agent: parse specs and mock-generate test cases."""

from __future__ import annotations

import json
import operator
from typing import Annotated, TypedDict

import yaml
from langgraph.graph import END, StateGraph


class AgentState(TypedDict):
    """Global state shared across OpenAPI agent graph nodes."""

    openapi_spec: str
    parsed_spec: dict
    test_cases: Annotated[list[dict], operator.add]
    error_logs: Annotated[list[str], operator.add]


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
                    f"Parsed OpenAPI root must be a mapping/object, got {type(parsed_data).__name__}."
                ]
            }
        return {"parsed_spec": parsed_data}
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        return {"error_logs": [f"Failed to parse openapi_spec: {exc}"]}
    except Exception as exc:  # noqa: BLE001 — surface unexpected parse errors into state
        return {"error_logs": [f"Unexpected error while parsing openapi_spec: {exc}"]}


def generate_cases_node(state: AgentState) -> dict:
    """Mock-generate basic HTTP test cases from the parsed OpenAPI paths."""
    parsed = state.get("parsed_spec") or {}
    if not parsed:
        hint = state.get("error_logs") or ["No parsed_spec available."]
        return {
            "error_logs": [
                "generate_cases skipped: parsed_spec is empty. "
                f"Prior errors: {'; '.join(hint)}"
            ]
        }

    paths = parsed.get("paths") or {}
    generated_cases: list[dict] = []

    if isinstance(paths, dict):
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, operation in methods.items():
                if method.startswith("x-") or not isinstance(operation, dict):
                    continue
                method_upper = method.upper()
                if method_upper not in {
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                    "HEAD",
                    "OPTIONS",
                }:
                    continue
                op_id = operation.get("operationId") or f"{method_upper.lower()}_{path.strip('/').replace('/', '_') or 'root'}"
                generated_cases.append(
                    {
                        "name": f"test_{op_id}",
                        "path": path,
                        "method": method_upper,
                    }
                )

    if not generated_cases:
        # Fallback demo case so the graph always produces a visible result
        generated_cases = [
            {"name": "test_health", "path": "/health", "method": "GET"},
        ]

    return {"test_cases": generated_cases}


workflow = StateGraph(AgentState)
workflow.add_node("parse_spec", parse_spec_node)
workflow.add_node("generate_cases", generate_cases_node)
workflow.set_entry_point("parse_spec")
workflow.add_edge("parse_spec", "generate_cases")
workflow.add_edge("generate_cases", END)
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
      responses:
        "200":
          description: JWT issued
"""

    initial_state: AgentState = {
        "openapi_spec": sample_openapi_yaml,
        "parsed_spec": {},
        "test_cases": [],
        "error_logs": [],
    }
    result = app.invoke(initial_state)
    print("test_cases:", result.get("test_cases"))
    print("error_logs:", result.get("error_logs"))
