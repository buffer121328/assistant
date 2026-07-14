from __future__ import annotations

from dataclasses import dataclass
import json
import math
from types import MappingProxyType
from typing import Mapping

from packages.capabilities import CapabilityKind, CapabilityRegistry
from packages.model_gateway import GatewayMessage


AGENT_PROFILE_TASK_TYPES: Mapping[str, str] = MappingProxyType(
    {
        "profile.daily": "daily",
        "profile.learn": "learn",
        "profile.office": "office",
        "profile.plan": "plan",
    }
)
_DECISION_FIELDS = frozenset({"capability_id", "confidence", "reason"})
_MAX_REASON_LENGTH = 300


class AgentRoutingError(ValueError):
    pass


class NoAgentRouteCandidatesError(AgentRoutingError):
    pass


class InvalidAgentRouteDecisionError(AgentRoutingError):
    pass


class AgentRouteModelError(AgentRoutingError):
    pass


@dataclass(frozen=True)
class AgentRouteCandidate:
    capability_id: str
    task_type: str
    display_name: str
    summary: str


@dataclass(frozen=True)
class AgentRouteDecision:
    capability_id: str
    task_type: str
    confidence: float
    reason: str


def build_agent_route_candidates(
    registry: CapabilityRegistry,
) -> tuple[AgentRouteCandidate, ...]:
    candidates = tuple(
        AgentRouteCandidate(
            capability_id=metadata.id,
            task_type=AGENT_PROFILE_TASK_TYPES[metadata.id],
            display_name=metadata.display_name,
            summary=metadata.summary,
        )
        for metadata in registry.list(
            kind=CapabilityKind.AGENT_PROFILE,
            enabled=True,
        )
        if metadata.id in AGENT_PROFILE_TASK_TYPES
    )
    if not candidates:
        raise NoAgentRouteCandidatesError("No enabled Agent route candidates")
    return candidates


def build_agent_route_messages(
    *,
    input_text: str,
    candidates: tuple[AgentRouteCandidate, ...],
) -> tuple[GatewayMessage, ...]:
    payload = {
        "input": input_text,
        "candidates": [
            {
                "capability_id": candidate.capability_id,
                "display_name": candidate.display_name,
                "summary": candidate.summary,
            }
            for candidate in candidates
        ],
    }
    return (
        GatewayMessage(
            role="system",
            content=(
                "Select exactly one allowed Agent Profile for the request. "
                "Return one JSON object only with capability_id, confidence from "
                "0 through 1, and a short non-empty reason. Do not select tools, "
                "skills, code, or invent capability IDs."
            ),
        ),
        GatewayMessage(
            role="user",
            content=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        ),
    )


def parse_agent_route_decision(
    content: str,
    candidates: tuple[AgentRouteCandidate, ...],
) -> AgentRouteDecision:
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise _invalid_decision() from exc

    if not isinstance(payload, dict) or set(payload) != _DECISION_FIELDS:
        raise _invalid_decision()

    capability_id = payload.get("capability_id")
    confidence = payload.get("confidence")
    reason = payload.get("reason")
    if (
        not isinstance(capability_id, str)
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
        or not isinstance(reason, str)
        or not reason.strip()
        or len(reason.strip()) > _MAX_REASON_LENGTH
    ):
        raise _invalid_decision()

    allowed = {candidate.capability_id: candidate for candidate in candidates}
    try:
        selected = allowed[capability_id]
    except KeyError as exc:
        raise _invalid_decision() from exc

    return AgentRouteDecision(
        capability_id=selected.capability_id,
        task_type=selected.task_type,
        confidence=float(confidence),
        reason=reason.strip(),
    )


def _invalid_decision() -> InvalidAgentRouteDecisionError:
    return InvalidAgentRouteDecisionError("Invalid Agent route decision")
