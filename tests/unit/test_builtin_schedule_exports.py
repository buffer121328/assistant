from __future__ import annotations


def test_schedule_package_exports_main_symbols() -> None:
    """schedule 子包直接导出拆分后的主实现。"""
    from tools.builtin import schedule as schedule_package

    assert schedule_package.AgentScheduleService is not None
    assert callable(schedule_package.build_schedule_tool_descriptors)
    assert callable(schedule_package.build_schedule_tool_specs)
    assert schedule_package.SCHEDULE_TOOL_VERSION
