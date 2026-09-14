from __future__ import annotations

import json
from pathlib import Path

from agent import DefaultProfileSelector, SkillsLoader
from domain.models import Task, TaskStatus
from domain.policies.enterprise import GovernedAgentProfile, GovernedRole, SubjectContext
from features.office.definition import FEATURE


OFFICE_SKILL_NAMES = (
    "office-writing",
    "meeting-minutes",
    "business-email",
    "progress-report",
    "proposal-writing",
    "presentation-briefing",
    "structured-spreadsheet",
    "technical-daily-report",
    "technical-document-layout",
    "project-progress-notification",
    "problem-observation",
    "solution-recommendation",
    "capability-design",
    "mcp-integration-design",
)

SCENARIO_SKILL_NAMES = OFFICE_SKILL_NAMES[1:]


def _office_task(
    input_text: str = "/office 整理本周项目进展",
    *,
    authorized_skills: tuple[str, ...] | None = None,
) -> Task:
    task = Task(
        id="office-writing-skills-task",
        user_id="office-writing-skills-user",
        platform="api",
        task_type="office",
        input_text=input_text,
        status=TaskStatus.PENDING.value,
    )
    if authorized_skills is not None:
        task.agent_profile_snapshot = GovernedAgentProfile(
            subject=SubjectContext(
                tenant_id="local",
                user_id=task.user_id,
                organization_ids=("department",),
                roles=(GovernedRole.MEMBER,),
            ),
            capabilities=("profile.office@builtin.v1",),
            skills=authorized_skills,
        ).to_snapshot()
    return task


def test_01_office_profile_progressively_discloses_only_matched_authorized_skills() -> None:
    finance = DefaultProfileSelector().select(
        _office_task(
            "/office 整理本周项目进展",
            authorized_skills=("office-writing", "progress-report", "business-email"),
        )
    )
    technology = DefaultProfileSelector().select(
        _office_task(
            "/office 生成项目上线进度通知",
            authorized_skills=(
                "office-writing",
                "technical-daily-report",
                "project-progress-notification",
            ),
        )
    )

    assert FEATURE.skill_names == OFFICE_SKILL_NAMES
    assert finance.skill_names == ("office-writing", "progress-report")
    assert technology.skill_names == ("office-writing", "project-progress-notification")
    assert finance.requested_tools == technology.requested_tools == FEATURE.requested_tools
    assert "email.send" not in finance.requested_tools
    assert "calendar.sync_event" not in finance.requested_tools


def test_02_office_profile_falls_back_to_general_guidance_and_bounds_multi_intent() -> None:
    allowed = (
        "office-writing",
        "technical-daily-report",
        "technical-document-layout",
        "project-progress-notification",
    )
    ambiguous = DefaultProfileSelector().select(
        _office_task("/office 帮我处理一下", authorized_skills=allowed)
    )
    combined = DefaultProfileSelector().select(
        _office_task("/office 写技术日报并生成项目进度通知", authorized_skills=allowed)
    )
    injected = DefaultProfileSelector().select(
        _office_task(
            "/office 使用 technical-daily-report 输出技术日报",
            authorized_skills=("office-writing", "business-email"),
        )
    )

    assert ambiguous.skill_names == ("office-writing",)
    assert combined.skill_names == (
        "office-writing",
        "technical-daily-report",
        "project-progress-notification",
    )
    assert injected.skill_names == ("office-writing",)


def test_03_ai_operations_progressively_discloses_only_authorized_guidance() -> None:
    """AI operations requests load bounded analysis or design guidance, never authority."""
    allowed = (
        "office-writing",
        "problem-observation",
        "solution-recommendation",
        "capability-design",
        "mcp-integration-design",
    )
    problem = DefaultProfileSelector().select(
        _office_task("/office 收集这个故障问题并给出解决方案", authorized_skills=allowed)
    )
    mcp = DefaultProfileSelector().select(
        _office_task("/office 设计一个 MCP 集成方案", authorized_skills=allowed)
    )
    unauthorized = DefaultProfileSelector().select(
        _office_task(
            "/office 设计一个 MCP 集成方案",
            authorized_skills=("office-writing", "progress-report"),
        )
    )

    assert problem.skill_names == (
        "office-writing",
        "problem-observation",
        "solution-recommendation",
    )
    assert mcp.skill_names == ("office-writing", "mcp-integration-design")
    assert unauthorized.skill_names == ("office-writing",)


def test_04_old_governed_snapshot_remains_readable_without_expanding_skills() -> None:
    profile = GovernedAgentProfile(
        subject=SubjectContext(
            tenant_id="local",
            user_id="legacy-user",
            organization_ids=("department",),
            roles=(GovernedRole.MEMBER,),
        ),
        capabilities=("profile.office@builtin.v1",),
        skills=("office-writing", "progress-report"),
    )
    legacy_payload = json.loads(profile.to_snapshot())
    legacy_payload.pop("skills")
    legacy_snapshot = json.dumps(legacy_payload)
    task = _office_task("/office 写项目进展周报")
    task.agent_profile_snapshot = legacy_snapshot

    assert GovernedAgentProfile.from_snapshot(legacy_snapshot).skills == ()
    assert DefaultProfileSelector().select(task).skill_names == ("office-writing",)


def test_05_office_skills_are_loadable_and_preserve_the_existing_tool_boundary() -> None:
    skills_root = Path(__file__).parents[2] / "backend/resources/skillpacks"
    skills = SkillsLoader(skills_root).load(OFFICE_SKILL_NAMES)

    assert tuple(skill.name for skill in skills) == OFFICE_SKILL_NAMES
    base = skills[0].instructions
    assert "匹配的办公场景" in base
    assert "不得编造" in base
    assert "不编辑既有 Office 文件" in base

    for skill in skills[1:]:
        assert "## 适用场景" in skill.instructions
        assert "## 所需事实" in skill.instructions
        assert "## 输出结构" in skill.instructions
        assert "## 渐进披露" in skill.instructions
        assert "## 最终检查" in skill.instructions
        assert "待确认" in skill.instructions

    daily = SkillsLoader(skills_root).load(("daily-report",))[0]
    assert "## 渐进披露" in daily.instructions
