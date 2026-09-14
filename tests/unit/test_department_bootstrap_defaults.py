from application.department_bootstrap import DEMO_CAPABILITY_SKILLS, DEPARTMENT_TEMPLATES


def test_demo_departments_use_governed_market_skills() -> None:
    names = {template.name for template in DEPARTMENT_TEMPLATES}
    assert "市场部" in names
    assert "技术部" not in names
    ai_skills = DEMO_CAPABILITY_SKILLS["capability.ai.improvement-operations"]
    assert {"skill-creator", "skill-installer"}.issubset(ai_skills)
    assert "gh-fix-ci" not in ai_skills
    assert "gh-address-comments" not in ai_skills
