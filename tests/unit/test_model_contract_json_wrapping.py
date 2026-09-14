from __future__ import annotations

import json

import pytest

from agent.prompting.model_contract import (
    AgentDecisionError,
    parse_agent_decision,
    parse_work_plan,
)


def work_plan_payload() -> str:
    return json.dumps(
        {
            "goal": "学习 LangChain",
            "steps": [
                {
                    "objective": "理解核心概念",
                    "acceptance_criteria": ["能说明链和图的区别"],
                    "agent_role": "researcher",
                }
            ],
        },
        ensure_ascii=False,
    )


def test_work_plan_accepts_json_fenced_by_markdown() -> None:
    parsed = parse_work_plan(f"下面是计划：\n```json\n{work_plan_payload()}\n```\n")

    assert parsed.goal == "学习 LangChain"
    assert parsed.steps[0].objective == "理解核心概念"


def test_final_decision_accepts_json_surrounded_by_explanation() -> None:
    parsed = parse_agent_decision(
        '我会直接回答：\n```json\n{"action":"final","answer":"这是普通回答。","plan":[]}\n```\n'
    )

    assert parsed.action == "final"
    assert parsed.answer == "这是普通回答。"


def test_wrapped_parser_rejects_ambiguous_multiple_objects() -> None:
    with pytest.raises(AgentDecisionError, match="must be valid JSON"):
        parse_work_plan(f"{work_plan_payload()}\n{work_plan_payload()}")

def test_final_decision_normalizes_safe_answer_alias_without_tools() -> None:
    parsed = parse_agent_decision(
        '{"action":"answer","answer":"这是普通回答。","plan":[]}'
    )

    assert parsed.action == "final"
    assert parsed.answer == "这是普通回答。"


def test_final_decision_accepts_single_item_json_array() -> None:
    parsed = parse_agent_decision(
        '[{"action":"final","answer":"这是普通回答。","plan":[]}]'
    )

    assert parsed.action == "final"
    assert parsed.answer == "这是普通回答。"


def test_final_decision_accepts_recognized_result_envelope() -> None:
    parsed = parse_agent_decision(
        '{"response":{"action":"final","answer":"这是普通回答。","plan":[]},"request_id":"safe-id"}'
    )

    assert parsed.action == "final"
    assert parsed.answer == "这是普通回答。"


def test_wrapped_parser_rejects_multiple_candidate_decisions() -> None:
    with pytest.raises(AgentDecisionError, match="must be valid JSON"):
        parse_agent_decision(
            '[{"action":"final","answer":"第一份回答","plan":[]},'
            '{"action":"final","answer":"第二份回答","plan":[]}]'
        )


def test_agent_decision_error_identifies_json_extraction_failure() -> None:
    with pytest.raises(AgentDecisionError) as captured:
        parse_agent_decision("not-json")

    assert captured.value.failure_category == "json_extraction"


def test_agent_decision_error_identifies_contract_validation_failure() -> None:
    with pytest.raises(AgentDecisionError) as captured:
        parse_agent_decision('{"action":"unknown","answer":"回答","plan":[]}')

    assert captured.value.failure_category == "contract_validation"
