"""TOS-DEV10-I04 / I04R1 Teacher OS development demo scenario (A–F).

NON_PRODUCTION. Synthetic tenant/principal only, reusing TOS-DEV01 identities.

Final loaded state keeps independently inspectable lifecycle examples:

  1. REVIEW WORK — TeachingWork + generated artifact left IN_REVIEW
  2. LIBRARY/PUBLISHED WORK — separate TeachingWork, approved + published
  3. ASSIGN/TEACH/ASSESS — uses the published work
  4. REMEDIATION — Improve command from class-level assessment
  5. Teacher Memory — ACTIVE HUMAN preference profile
  6. Assistant context — governed records for mission/work/library/assess/etc.

Never runs on application startup, migration, worker startup, or production
composition. The CLI is the only entry point.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine

from aieos.development.teacher_os_review_scenario import (
    SYNTHETIC_PRINCIPAL_ID,
    SYNTHETIC_TENANT_ID,
)

NON_PRODUCTION = True

__all__ = [
    "NON_PRODUCTION",
    "SCENARIO_ID",
    "SYNTHETIC_PRINCIPAL_ID",
    "SYNTHETIC_TENANT_ID",
    "CLASS_REF",
    "GOAL_REVIEW",
    "GOAL_PUBLISH",
    "GOAL_REMEDIATE",
    "DemoReport",
    "ensure_synthetic_human_principal",
    "ensure_teacher_os_demo",
    "write_demo_report",
]

SCENARIO_ID = "tos-dev10-i04-teacher-os-demo"
CLASS_REF = "class-5a"
INTENT_PREPARE_TOMORROW = "prepare_tomorrow"

GOAL_REVIEW = (
    "[TOS-DEV10-I04R1:review] Prepare Grade 5 fractions review-queue demo so "
    "Review remains pending with an IN_REVIEW preparation artefact."
)
GOAL_PUBLISH = (
    "[TOS-DEV10-I04R1:library] Prepare Grade 5 fractions library demo so a "
    "separate published worksheet is visible in Library."
)
GOAL_REMEDIATE = (
    "[TOS-DEV10-I04R1:remediate] Re-teach comparing fractions with concrete "
    "visual models after class-level MIXED evidence."
)

StatusFlag = Literal["created", "reused", "updated"]


@dataclass(slots=True)
class StepStatus:
    key: str
    status: StatusFlag
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DemoReport:
    scenario_id: str
    tenant_id: str
    principal_id: str
    scenario_date: str
    target_date: str
    class_ref: str
    review_work_id: str | None
    review_content_id: str | None
    review_version_id: str | None
    published_work_id: str | None
    published_content_id: str | None
    published_version_id: str | None
    assignment_id: str | None
    execution_id: str | None
    assessment_id: str | None
    remediation_work_id: str | None
    memory_id: str | None
    steps: list[StepStatus]
    reused_existing: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "tenant_id": self.tenant_id,
            "principal_id": self.principal_id,
            "scenario_date": self.scenario_date,
            "target_date": self.target_date,
            "class_ref": self.class_ref,
            "review_work_id": self.review_work_id,
            "review_content_id": self.review_content_id,
            "review_version_id": self.review_version_id,
            "published_work_id": self.published_work_id,
            "published_content_id": self.published_content_id,
            "published_version_id": self.published_version_id,
            "assignment_id": self.assignment_id,
            "execution_id": self.execution_id,
            "assessment_id": self.assessment_id,
            "remediation_work_id": self.remediation_work_id,
            "memory_id": self.memory_id,
            "reused_existing": self.reused_existing,
            "steps": [asdict(step) for step in self.steps],
        }


def ensure_synthetic_human_principal(
    bootstrap_engine: Engine,
    principal_id: UUID = SYNTHETIC_PRINCIPAL_ID,
) -> None:
    """Upsert ACTIVE HUMAN principal via bootstrap role (runtime cannot INSERT)."""
    with bootstrap_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO security.principals (
                    principal_id, status, principal_kind, created_at, updated_at
                ) VALUES (
                    :principal_id, 'ACTIVE', 'HUMAN',
                    clock_timestamp(), clock_timestamp()
                )
                ON CONFLICT (principal_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    principal_kind = EXCLUDED.principal_kind,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {"principal_id": principal_id},
        )


def _idem_key(step: str) -> str:
    return f"tos-dev10-i04r1:{SCENARIO_ID}:{SYNTHETIC_TENANT_ID}:{step}"


def _headers(
    tenant_id: UUID,
    *,
    idempotency_key: str | None = None,
    if_match: str | None = None,
) -> dict[str, str]:
    out = {"X-AIEOS-Tenant-ID": str(tenant_id)}
    if idempotency_key is not None:
        out["Idempotency-Key"] = idempotency_key
    if if_match is not None:
        out["If-Match"] = if_match
    return out


def _list_works(client: Any, tenant_id: UUID) -> list[dict[str, Any]]:
    response = client.get(
        "/api/v1/teaching/works",
        params={"limit": 100},
        headers=_headers(tenant_id),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"teaching works list failed: {response.status_code} {response.text}"
        )
    return list(response.json()["items"])


def _find_work(items: list[dict[str, Any]], *, goal_text: str) -> dict[str, Any] | None:
    for item in items:
        if item.get("goal_text") == goal_text:
            return item
    return None


def _get_work(client: Any, tenant_id: UUID, work_id: str) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/teaching/works/{work_id}",
        headers=_headers(tenant_id),
    )
    if response.status_code != 200:
        raise RuntimeError(f"teaching work get failed: {response.text}")
    body = response.json()
    body["_etag"] = response.headers.get("ETag", f'"r{body["aggregate_revision"]}"')
    return body


def _ensure_work(
    client: Any,
    tenant_id: UUID,
    *,
    goal_text: str,
    topic: str,
    target_date: date,
    step_key: str,
    idem_step: str,
    steps: list[StepStatus],
) -> tuple[str, str, bool]:
    existing = _find_work(_list_works(client, tenant_id), goal_text=goal_text)
    if existing is not None:
        work = _get_work(client, tenant_id, existing["work_id"])
        steps.append(
            StepStatus(
                key=step_key,
                status="reused",
                detail={"work_id": work["work_id"]},
            )
        )
        return work["work_id"], work["_etag"], True

    created = client.post(
        "/api/v1/teaching/works",
        json={
            "intent_type": INTENT_PREPARE_TOMORROW,
            "goal_text": goal_text,
            "target_date": target_date.isoformat(),
            "locale": "en-IN",
            "class_label": "Grade 5A",
            "subject": "Mathematics",
            "topic": topic,
        },
        headers=_headers(tenant_id, idempotency_key=_idem_key(idem_step)),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"teaching work create failed ({step_key}): {created.text}")
    body = created.json()
    steps.append(
        StepStatus(
            key=step_key,
            status="created",
            detail={"work_id": body["work_id"]},
        )
    )
    return body["work_id"], created.headers["ETag"], False


def _list_artifacts(
    client: Any, tenant_id: UUID, work_id: str
) -> list[dict[str, Any]]:
    response = client.get(
        f"/api/v1/teaching/works/{work_id}/artifacts",
        headers=_headers(tenant_id),
    )
    if response.status_code != 200:
        raise RuntimeError(f"artifacts list failed: {response.text}")
    return list(response.json()["items"])


def _ensure_generate(
    client: Any,
    tenant_id: UUID,
    *,
    work_id: str,
    work_etag: str,
    step_key: str,
    idem_step: str,
    steps: list[StepStatus],
) -> tuple[str, str, bool]:
    artifacts = _list_artifacts(client, tenant_id, work_id)
    if artifacts:
        artifact = artifacts[0]
        steps.append(
            StepStatus(
                key=step_key,
                status="reused",
                detail={
                    "content_id": artifact["content_id"],
                    "version_id": artifact["version_id"],
                    "stewardship_state": artifact.get("stewardship_state"),
                },
            )
        )
        return artifact["content_id"], artifact["version_id"], True

    generated = client.post(
        f"/api/v1/teaching/works/{work_id}/actions/generate",
        headers=_headers(
            tenant_id,
            idempotency_key=_idem_key(idem_step),
            if_match=work_etag,
        ),
    )
    if generated.status_code == 409:
        artifacts = _list_artifacts(client, tenant_id, work_id)
        if not artifacts:
            raise RuntimeError("generate already exists but no artifacts found")
        artifact = artifacts[0]
        steps.append(
            StepStatus(
                key=step_key,
                status="reused",
                detail={
                    "content_id": artifact["content_id"],
                    "version_id": artifact["version_id"],
                    "stewardship_state": artifact.get("stewardship_state"),
                },
            )
        )
        return artifact["content_id"], artifact["version_id"], True
    if generated.status_code != 200:
        raise RuntimeError(
            f"generate failed ({step_key}): {generated.status_code} {generated.text}"
        )
    body = generated.json()["artifact"]
    steps.append(
        StepStatus(
            key=step_key,
            status="created",
            detail={
                "content_id": body["content_id"],
                "version_id": body["version_id"],
                "stewardship_state": body.get("stewardship_state"),
            },
        )
    )
    return body["content_id"], body["version_id"], False


def _get_content(client: Any, tenant_id: UUID, content_id: str) -> dict[str, Any]:
    response = client.get(
        f"/api/v1/contents/{content_id}",
        headers=_headers(tenant_id),
    )
    if response.status_code != 200:
        raise RuntimeError(f"content get failed: {response.text}")
    body = response.json()
    body["_etag"] = response.headers.get(
        "ETag", f'"r{body["aggregate_revision"]}"'
    )
    return body


def _assert_review_pending(
    client: Any,
    tenant_id: UUID,
    *,
    content_id: str,
    version_id: str,
    steps: list[StepStatus],
) -> None:
    content = _get_content(client, tenant_id, content_id)
    state = content.get("stewardship_state")
    if state != "IN_REVIEW":
        raise RuntimeError(
            f"review demo must remain IN_REVIEW; got stewardship_state={state!r}"
        )
    if content.get("published_version_id") is not None:
        raise RuntimeError("review demo must not be published")

    queue = client.get(
        "/api/v1/teacher-os/review-queue",
        params={"limit": 100},
        headers=_headers(tenant_id),
    )
    if queue.status_code != 200:
        raise RuntimeError(f"review-queue list failed: {queue.text}")
    found = any(
        item.get("content_id") == content_id
        and item.get("version_id") == version_id
        for item in queue.json()["items"]
    )
    if not found:
        raise RuntimeError("review demo artifact missing from Review Queue")

    steps.append(
        StepStatus(
            key="A.review_pending",
            status="reused",
            detail={
                "content_id": content_id,
                "version_id": version_id,
                "stewardship_state": "IN_REVIEW",
                "in_review_queue": True,
            },
        )
    )


def _ensure_approve_and_publish(
    client: Any,
    tenant_id: UUID,
    *,
    content_id: str,
    version_id: str,
    steps: list[StepStatus],
) -> tuple[bool, bool]:
    content = _get_content(client, tenant_id, content_id)
    approved_reused = True
    published_reused = True

    if content.get("stewardship_state") == "IN_REVIEW":
        detail = client.get(
            f"/api/v1/teacher-os/review-queue/{content_id}/versions/{version_id}",
            headers=_headers(tenant_id),
        )
        if detail.status_code != 200:
            raise RuntimeError(f"review detail failed: {detail.text}")
        approved = client.post(
            f"/api/v1/contents/{content_id}/versions/{version_id}/actions/approve",
            json={},
            headers=_headers(
                tenant_id,
                idempotency_key=_idem_key("library-approve"),
                if_match=detail.headers["ETag"],
            ),
        )
        if approved.status_code != 200:
            raise RuntimeError(f"approve failed: {approved.text}")
        approved_reused = False
        steps.append(
            StepStatus(
                key="B.approve",
                status="updated",
                detail={"content_id": content_id, "version_id": version_id},
            )
        )
        content = _get_content(client, tenant_id, content_id)
    else:
        steps.append(
            StepStatus(
                key="B.approve",
                status="reused",
                detail={
                    "content_id": content_id,
                    "stewardship_state": content.get("stewardship_state"),
                },
            )
        )

    if content.get("published_version_id") == version_id:
        steps.append(
            StepStatus(
                key="B.publish",
                status="reused",
                detail={"content_id": content_id, "version_id": version_id},
            )
        )
        return approved_reused, True

    if content.get("stewardship_state") not in {"APPROVED", "PUBLISHED"}:
        raise RuntimeError(
            f"expected APPROVED/PUBLISHED stewardship; got {content.get('stewardship_state')}"
        )

    published = client.post(
        f"/api/v1/contents/{content_id}/actions/publish",
        json={"version_id": version_id},
        headers=_headers(
            tenant_id,
            idempotency_key=_idem_key("library-publish"),
            if_match=content["_etag"],
        ),
    )
    if published.status_code != 200:
        raise RuntimeError(f"publish failed: {published.text}")
    published_reused = False
    steps.append(
        StepStatus(
            key="B.publish",
            status="created",
            detail={"content_id": content_id, "version_id": version_id},
        )
    )
    return approved_reused, published_reused


def _assert_library_visible(
    client: Any,
    tenant_id: UUID,
    *,
    content_id: str,
    version_id: str,
    steps: list[StepStatus],
) -> None:
    library = client.get(
        "/api/v1/teacher-os/library",
        params={"limit": 100, "published_only": True},
        headers=_headers(tenant_id),
    )
    if library.status_code != 200:
        raise RuntimeError(f"library list failed: {library.text}")
    found = None
    for item in library.json()["items"]:
        if item.get("content_id") == content_id:
            found = item
            break
    if found is None:
        raise RuntimeError("published demo content missing from Library")
    if found.get("published_version_id") != version_id:
        raise RuntimeError(
            "library published_version_id does not match demo published version"
        )
    steps.append(
        StepStatus(
            key="B.library_visible",
            status="reused",
            detail={
                "content_id": content_id,
                "published_version_id": version_id,
            },
        )
    )


def _ensure_assignment(
    client: Any,
    tenant_id: UUID,
    *,
    work_id: str,
    content_id: str,
    version_id: str,
    steps: list[StepStatus],
) -> tuple[str, bool]:
    listed = client.get(
        "/api/v1/teaching/assignments",
        params={"limit": 100},
        headers=_headers(tenant_id),
    )
    if listed.status_code != 200:
        raise RuntimeError(f"assignments list failed: {listed.text}")
    for item in listed.json()["items"]:
        if (
            item.get("content_id") == content_id
            and item.get("content_version_id") == version_id
            and item.get("class_ref") == CLASS_REF
            and item.get("source_work_id") == work_id
            and item.get("lifecycle_state") == "ACTIVE"
        ):
            steps.append(
                StepStatus(
                    key="C.assignment",
                    status="reused",
                    detail={"assignment_id": item["assignment_id"]},
                )
            )
            return item["assignment_id"], True

    created = client.post(
        "/api/v1/teaching/assignments",
        json={
            "content_id": content_id,
            "content_version_id": version_id,
            "class_ref": CLASS_REF,
            "source_work_id": work_id,
        },
        headers=_headers(tenant_id, idempotency_key=_idem_key("assignment")),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"assignment create failed: {created.text}")
    assignment_id = created.json()["assignment_id"]
    steps.append(
        StepStatus(
            key="C.assignment",
            status="created",
            detail={"assignment_id": assignment_id},
        )
    )
    return assignment_id, False


def _ensure_execution(
    client: Any,
    tenant_id: UUID,
    *,
    work_id: str,
    content_id: str,
    version_id: str,
    steps: list[StepStatus],
) -> tuple[str, bool]:
    listed = client.get(
        "/api/v1/teaching/executions",
        params={"limit": 100},
        headers=_headers(tenant_id),
    )
    if listed.status_code != 200:
        raise RuntimeError(f"executions list failed: {listed.text}")
    for item in listed.json()["items"]:
        if (
            item.get("work_id") == work_id
            and item.get("class_ref") == CLASS_REF
            and item.get("lifecycle_state") == "COMPLETED"
        ):
            steps.append(
                StepStatus(
                    key="C.execution",
                    status="reused",
                    detail={
                        "execution_id": item["execution_id"],
                        "lifecycle_state": item["lifecycle_state"],
                    },
                )
            )
            return item["execution_id"], True

    in_progress = None
    for item in listed.json()["items"]:
        if (
            item.get("work_id") == work_id
            and item.get("class_ref") == CLASS_REF
            and item.get("lifecycle_state") == "IN_PROGRESS"
        ):
            in_progress = item
            break

    if in_progress is None:
        created = client.post(
            "/api/v1/teaching/executions",
            json={
                "work_id": work_id,
                "class_ref": CLASS_REF,
                "bindings": [
                    {
                        "content_id": content_id,
                        "content_version_id": version_id,
                        "artifact_kind": "worksheet",
                    }
                ],
            },
            headers=_headers(tenant_id, idempotency_key=_idem_key("execution")),
        )
        if created.status_code not in (200, 201):
            raise RuntimeError(f"execution start failed: {created.text}")
        execution_id = created.json()["execution_id"]
        etag = created.headers["ETag"]
        started_new = True
    else:
        execution_id = in_progress["execution_id"]
        detail = client.get(
            f"/api/v1/teaching/executions/{execution_id}",
            headers=_headers(tenant_id),
        )
        if detail.status_code != 200:
            raise RuntimeError(f"execution get failed: {detail.text}")
        etag = detail.headers.get(
            "ETag", f'"r{detail.json()["aggregate_revision"]}"'
        )
        started_new = False

    completed = client.post(
        f"/api/v1/teaching/executions/{execution_id}/actions/complete",
        headers=_headers(
            tenant_id,
            idempotency_key=_idem_key("execution-complete"),
            if_match=etag,
        ),
    )
    if completed.status_code != 200:
        raise RuntimeError(f"execution complete failed: {completed.text}")
    steps.append(
        StepStatus(
            key="C.execution",
            status="created" if started_new else "updated",
            detail={
                "execution_id": execution_id,
                "lifecycle_state": "COMPLETED",
            },
        )
    )
    return execution_id, False


def _ensure_assessment(
    client: Any,
    tenant_id: UUID,
    *,
    work_id: str,
    content_id: str,
    version_id: str,
    execution_id: str,
    assignment_id: str,
    steps: list[StepStatus],
) -> tuple[str, int, bool]:
    listed = client.get(
        "/api/v1/assessment/classroom-assessments",
        params={"limit": 100, "work_id": work_id, "class_ref": CLASS_REF},
        headers=_headers(tenant_id),
    )
    if listed.status_code != 200:
        raise RuntimeError(f"assessments list failed: {listed.text}")
    for item in listed.json()["items"]:
        if (
            item.get("content_id") == content_id
            and item.get("content_version_id") == version_id
            and item.get("lifecycle_state") == "RECORDED"
        ):
            steps.append(
                StepStatus(
                    key="D.assessment",
                    status="reused",
                    detail={"assessment_id": item["assessment_id"]},
                )
            )
            return item["assessment_id"], int(item["aggregate_revision"]), True

    created = client.post(
        "/api/v1/assessment/classroom-assessments",
        json={
            "class_ref": CLASS_REF,
            "content_id": content_id,
            "content_version_id": version_id,
            "class_result_level": "MIXED",
            "class_result_note": (
                "[TOS-DEV10-I04R1] Synthetic MIXED class-level evidence for Improve."
            ),
            "work_id": work_id,
            "execution_id": execution_id,
            "assignment_id": assignment_id,
        },
        headers=_headers(tenant_id, idempotency_key=_idem_key("assessment")),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"assessment record failed: {created.text}")
    body = created.json()
    steps.append(
        StepStatus(
            key="D.assessment",
            status="created",
            detail={"assessment_id": body["assessment_id"]},
        )
    )
    return body["assessment_id"], int(body["aggregate_revision"]), False


def _ensure_remediation(
    client: Any,
    tenant_id: UUID,
    *,
    assessment_id: str,
    assessment_revision: int,
    target_date: date,
    steps: list[StepStatus],
) -> tuple[str, bool]:
    existing = _find_work(_list_works(client, tenant_id), goal_text=GOAL_REMEDIATE)
    if existing is not None:
        steps.append(
            StepStatus(
                key="D.remediation",
                status="reused",
                detail={"work_id": existing["work_id"]},
            )
        )
        return existing["work_id"], True

    created = client.post(
        "/api/v1/teaching/works/from-classroom-assessment",
        json={
            "assessment_id": assessment_id,
            "expected_assessment_aggregate_revision": assessment_revision,
            "goal_text": GOAL_REMEDIATE,
            "target_date": target_date.isoformat(),
            "locale": "en-IN",
            "subject": "Mathematics",
            "topic": "Comparing fractions",
        },
        headers=_headers(tenant_id, idempotency_key=_idem_key("remediation")),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"remediation create failed: {created.text}")
    work_id = created.json()["work_id"]
    steps.append(
        StepStatus(
            key="D.remediation",
            status="created",
            detail={"work_id": work_id},
        )
    )
    return work_id, False


def _ensure_memory(
    client: Any,
    tenant_id: UUID,
    steps: list[StepStatus],
) -> tuple[str, bool]:
    got = client.get("/api/v1/teacher-os/memory", headers=_headers(tenant_id))
    if got.status_code == 200:
        memory_id = got.json()["memory_id"]
        steps.append(
            StepStatus(
                key="E.memory",
                status="reused",
                detail={"memory_id": memory_id},
            )
        )
        return memory_id, True
    if got.status_code not in {404, 403}:
        raise RuntimeError(f"memory get failed: {got.status_code} {got.text}")

    created = client.post(
        "/api/v1/teacher-os/memory",
        json={
            "preferences": {
                "teaching_style": "balanced",
                "preferred_difficulty": "standard",
                "preparation_detail": "balanced",
                "output_format": "structured",
                "include_differentiation": True,
            }
        },
        headers=_headers(tenant_id, idempotency_key=_idem_key("memory")),
    )
    if created.status_code not in (200, 201):
        raise RuntimeError(f"memory create failed: {created.text}")
    memory_id = created.json()["memory_id"]
    steps.append(
        StepStatus(
            key="E.memory",
            status="created",
            detail={"memory_id": memory_id},
        )
    )
    return memory_id, False


def _mark_assistant_context_ready(
    *,
    review_work_id: str,
    published_work_id: str,
    published_content_id: str,
    assignment_id: str,
    execution_id: str,
    assessment_id: str,
    remediation_work_id: str,
    memory_id: str,
    steps: list[StepStatus],
) -> None:
    steps.append(
        StepStatus(
            key="F.assistant_context",
            status="reused",
            detail={
                "ready": True,
                "review_work_id": review_work_id,
                "published_work_id": published_work_id,
                "published_content_id": published_content_id,
                "assignment_id": assignment_id,
                "execution_id": execution_id,
                "assessment_id": assessment_id,
                "remediation_work_id": remediation_work_id,
                "memory_id": memory_id,
            },
        )
    )


def ensure_teacher_os_demo(
    client: Any,
    *,
    tenant_id: UUID = SYNTHETIC_TENANT_ID,
    principal_id: UUID = SYNTHETIC_PRINCIPAL_ID,
    scenario_date: date,
) -> DemoReport:
    """Idempotently ensure distinct lifecycle demo scenarios via HTTP contracts."""
    target_date = scenario_date + timedelta(days=1)
    steps: list[StepStatus] = []
    reused_flags: list[bool] = []

    # --- 1. REVIEW WORK (leave IN_REVIEW) ---
    review_work_id, review_etag, reused = _ensure_work(
        client,
        tenant_id,
        goal_text=GOAL_REVIEW,
        topic="Comparing fractions — review queue",
        target_date=target_date,
        step_key="A.review_work",
        idem_step="review-create-work",
        steps=steps,
    )
    reused_flags.append(reused)

    review_content_id, review_version_id, reused = _ensure_generate(
        client,
        tenant_id,
        work_id=review_work_id,
        work_etag=review_etag,
        step_key="A.review_generate",
        idem_step="review-generate",
        steps=steps,
    )
    reused_flags.append(reused)
    _assert_review_pending(
        client,
        tenant_id,
        content_id=review_content_id,
        version_id=review_version_id,
        steps=steps,
    )

    # --- 2. LIBRARY / PUBLISHED WORK (separate aggregate) ---
    published_work_id, published_etag, reused = _ensure_work(
        client,
        tenant_id,
        goal_text=GOAL_PUBLISH,
        topic="Comparing fractions — library",
        target_date=target_date,
        step_key="B.published_work",
        idem_step="library-create-work",
        steps=steps,
    )
    reused_flags.append(reused)

    published_content_id, published_version_id, reused = _ensure_generate(
        client,
        tenant_id,
        work_id=published_work_id,
        work_etag=published_etag,
        step_key="B.published_generate",
        idem_step="library-generate",
        steps=steps,
    )
    reused_flags.append(reused)

    approved_reused, published_reused = _ensure_approve_and_publish(
        client,
        tenant_id,
        content_id=published_content_id,
        version_id=published_version_id,
        steps=steps,
    )
    reused_flags.extend([approved_reused, published_reused])
    _assert_library_visible(
        client,
        tenant_id,
        content_id=published_content_id,
        version_id=published_version_id,
        steps=steps,
    )

    # Review artifact must still be pending after library publish path.
    review_after = _get_content(client, tenant_id, review_content_id)
    if review_after.get("stewardship_state") != "IN_REVIEW":
        raise RuntimeError(
            "review artifact must survive loader completion as IN_REVIEW; "
            f"got {review_after.get('stewardship_state')!r}"
        )
    if review_content_id == published_content_id:
        raise RuntimeError("review and published content IDs must be distinct")
    if review_work_id == published_work_id:
        raise RuntimeError("review and published work IDs must be distinct")

    # --- 3. ASSIGN / TEACH / ASSESS on published work ---
    assignment_id, reused = _ensure_assignment(
        client,
        tenant_id,
        work_id=published_work_id,
        content_id=published_content_id,
        version_id=published_version_id,
        steps=steps,
    )
    reused_flags.append(reused)

    execution_id, reused = _ensure_execution(
        client,
        tenant_id,
        work_id=published_work_id,
        content_id=published_content_id,
        version_id=published_version_id,
        steps=steps,
    )
    reused_flags.append(reused)

    assessment_id, assessment_revision, reused = _ensure_assessment(
        client,
        tenant_id,
        work_id=published_work_id,
        content_id=published_content_id,
        version_id=published_version_id,
        execution_id=execution_id,
        assignment_id=assignment_id,
        steps=steps,
    )
    reused_flags.append(reused)

    # --- 4. REMEDIATION ---
    remediation_work_id, reused = _ensure_remediation(
        client,
        tenant_id,
        assessment_id=assessment_id,
        assessment_revision=assessment_revision,
        target_date=target_date,
        steps=steps,
    )
    reused_flags.append(reused)

    # --- 5. Teacher Memory ---
    memory_id, reused = _ensure_memory(client, tenant_id, steps)
    reused_flags.append(reused)

    # --- 6. Assistant context marker ---
    _mark_assistant_context_ready(
        review_work_id=review_work_id,
        published_work_id=published_work_id,
        published_content_id=published_content_id,
        assignment_id=assignment_id,
        execution_id=execution_id,
        assessment_id=assessment_id,
        remediation_work_id=remediation_work_id,
        memory_id=memory_id,
        steps=steps,
    )

    return DemoReport(
        scenario_id=SCENARIO_ID,
        tenant_id=str(tenant_id),
        principal_id=str(principal_id),
        scenario_date=scenario_date.isoformat(),
        target_date=target_date.isoformat(),
        class_ref=CLASS_REF,
        review_work_id=review_work_id,
        review_content_id=review_content_id,
        review_version_id=review_version_id,
        published_work_id=published_work_id,
        published_content_id=published_content_id,
        published_version_id=published_version_id,
        assignment_id=assignment_id,
        execution_id=execution_id,
        assessment_id=assessment_id,
        remediation_work_id=remediation_work_id,
        memory_id=memory_id,
        steps=steps,
        reused_existing=all(reused_flags),
    )


def write_demo_report(report: DemoReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
