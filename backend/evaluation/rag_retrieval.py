from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from domain.models import Base, User
from rag import KnowledgeService

from .loader import EvaluationDataError


async def evaluate_rag_retrieval_fixture(path: Path) -> dict[str, Any]:
    """处理 evaluate rag retrieval fixture。

    Args:
        path: path 参数。
    """
    payload = _load_fixture(path)
    with TemporaryDirectory(prefix="assistant-rag-eval-") as directory:
        root = Path(directory) / "knowledge"
        database = Path(directory) / "rag-eval.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database}", poolclass=NullPool
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            report = await _run_fixture(payload, root=root, sessionmaker=sessionmaker)
        finally:
            await engine.dispose()
    return report


async def _run_fixture(
    payload: dict[str, Any],
    *,
    root: Path,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """执行 运行 fixture 的内部辅助逻辑。

    Args:
        payload: payload 参数。
        root: root 参数。
        sessionmaker: sessionmaker 参数。
    """
    async with sessionmaker() as session:
        user = User(display_name="V12 RAG evaluator")
        session.add(user)
        await session.flush()
        user_root = root / user.id
        user_root.mkdir(parents=True)
        service = KnowledgeService(session, import_root=root)
        for document in payload["documents"]:
            source = user_root / document["source_label"]
            source.write_text(document["content"], encoding="utf-8")
            await service.ingest(user_id=user.id, source=source)

        case_reports: list[dict[str, Any]] = []
        recall_values: list[float] = []
        abstention_checks: list[bool] = []
        injection_checks: list[bool] = []
        for case in payload["cases"]:
            results = await service.search(
                user_id=user.id,
                query=case["query"],
                limit=case.get("limit", 5),
            )
            actual_sources = tuple(dict.fromkeys(item.source_label for item in results))
            expected_sources = tuple(case["expected_sources"])
            expected_set = set(expected_sources)
            recall = (
                len(expected_set.intersection(actual_sources)) / len(expected_set)
                if expected_set
                else 1.0
            )
            answerable = bool(results)
            answerable_ok = answerable is case["expect_answerable"]
            risk_detected = any(item.instruction_risk for item in results)
            risk_ok = risk_detected is case["expect_instruction_risk"]
            source_ids_ok = all(
                item.source_id.startswith("knowledge:")
                and item.citation_token == f"[{item.source_id}]"
                and item.trust_boundary == "untrusted_document"
                for item in results
            )
            passed = recall == 1.0 and answerable_ok and risk_ok and source_ids_ok
            case_reports.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "recall_at_k": recall,
                    "answerable": answerable,
                    "answerable_ok": answerable_ok,
                    "instruction_risk_detected": risk_detected,
                    "instruction_risk_ok": risk_ok,
                    "actual_sources": list(actual_sources),
                }
            )
            if expected_sources:
                recall_values.append(recall)
            else:
                abstention_checks.append(answerable_ok)
            if case["expect_instruction_risk"]:
                injection_checks.append(risk_ok)

    return {
        "version": payload["version"],
        "passed": all(case["passed"] for case in case_reports),
        "case_count": len(case_reports),
        "metrics": {
            "mean_recall_at_k": (
                sum(recall_values) / len(recall_values) if recall_values else 1.0
            ),
            "abstention_accuracy": (
                sum(abstention_checks) / len(abstention_checks)
                if abstention_checks
                else 1.0
            ),
            "instruction_risk_accuracy": (
                sum(injection_checks) / len(injection_checks)
                if injection_checks
                else 1.0
            ),
        },
        "failed_cases": [case["id"] for case in case_reports if not case["passed"]],
        "cases": case_reports,
    }


def _load_fixture(path: Path) -> dict[str, Any]:
    """执行 加载 fixture 的内部辅助逻辑。

    Args:
        path: path 参数。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationDataError("unable to load RAG retrieval fixture") from exc
    if not isinstance(payload, dict) or payload.get("version") != "v12-06-retrieval":
        raise EvaluationDataError("RAG retrieval fixture version is invalid")
    documents = payload.get("documents")
    cases = payload.get("cases")
    if not isinstance(documents, list) or not documents:
        raise EvaluationDataError("RAG retrieval fixture documents are invalid")
    if not isinstance(cases, list) or not cases:
        raise EvaluationDataError("RAG retrieval fixture cases are invalid")
    for document in documents:
        if (
            not isinstance(document, Mapping)
            or not isinstance(document.get("source_label"), str)
            or not document["source_label"].endswith(".txt")
            or not isinstance(document.get("content"), str)
            or not document["content"].strip()
        ):
            raise EvaluationDataError("RAG retrieval fixture document is invalid")
    for case in cases:
        if (
            not isinstance(case, Mapping)
            or not isinstance(case.get("id"), str)
            or not isinstance(case.get("query"), str)
            or not case["query"].strip()
            or not isinstance(case.get("expected_sources"), list)
            or not all(isinstance(item, str) for item in case["expected_sources"])
            or not isinstance(case.get("expect_answerable"), bool)
            or not isinstance(case.get("expect_instruction_risk"), bool)
            or (
                "limit" in case
                and (not isinstance(case["limit"], int) or not 1 <= case["limit"] <= 20)
            )
        ):
            raise EvaluationDataError("RAG retrieval fixture case is invalid")
    return payload
