# TOS-DEV10-I04 — Teacher OS Assistant v1 + development demo

NON_PRODUCTION. Contextual Teacher OS Assistant is **READ / REASON / SUGGEST**
only. It does not persist chat, mutate Teacher Memory, Publish, Assign, start
Teach/execution, record Assessment, or create remediation.

Demo loader seeds scenarios **A–F** through published HTTP contracts for the
synthetic TOS-DEV01 teacher identities.

## Prerequisites

- Docker Desktop running
- Backend repo checkout on this branch
- Frontend repo available for UI verification
- Database URLs supplied from your local env / CLI args only — never paste
  credentials into this document

## 1. Start local Postgres

From `eduvijna-aieos-backend`:

```powershell
uv run python tools/dev/local_db.py up
uv run python tools/dev/local_db.py status
```

Default substrate host is `127.0.0.1:55432` (see `tools/dev/constants.py`).
Build runtime and bootstrap URLs from your local env — do not hard-code
passwords in scripts you commit.

## 2. Migrations to `tosd100001`

With the migrator URL from your local tooling:

```powershell
# Example pattern — substitute your migrator URL from env
$env:AIEOS_DATABASE_URL = "<migrator-sqlalchemy-url-from-env>"
uv run alembic upgrade head
uv run alembic current
```

Expected head: `tosd100001` (Teacher Memory). No chat migration is authorized
in this increment.

## 3. Load demo (scenarios A–F) — I04R1 distinct lifecycles

Explicit CLI only. Refuses when `AIEOS_ENVIRONMENT` / `ENVIRONMENT` is
`production`/`prod`. Defaults to localhost DB hosts; use
`--allow-remote-non-production` only for deliberate non-prod remote hosts.

```powershell
uv run python tools/development/load_teacher_os_demo.py `
  --database-url $env:AIEOS_RUNTIME_DATABASE_URL `
  --bootstrap-database-url $env:AIEOS_BOOTSTRAP_DATABASE_URL `
  --scenario-date 2026-09-06
```

Final loaded state keeps **independently inspectable** lifecycle examples
(I04R1). Review and Library are **separate** TeachingWorks / Content IDs.
Business aggregates are created only through published HTTP contracts (no
business-table SQL inserts). Bootstrap SQL may upsert the synthetic ACTIVE
HUMAN principal only.

| Step | Surface |
|------|---------|
| A | **REVIEW WORK** — TeachingWork + generate; artifact remains `IN_REVIEW` / pending Review (not approved or published) |
| B | **LIBRARY WORK** — separate TeachingWork + generate + approve + publish; visible in Library |
| C | Assignment + completed TeachingExecution on published work / `class-5a` |
| D | ClassroomAssessment (MIXED) + remediation TeachingWork via Improve |
| E | Teacher Memory preferences (ACTIVE HUMAN profile) |
| F | Enough authorized context for Assistant (mission/work/library/assess/remediation/Memory) |

`DemoReport` / `tmp/teacher-os-demo.json` exposes distinct IDs:
`review_work_id`, `review_content_id`, `review_version_id`,
`published_work_id`, `published_content_id`, `published_version_id`,
`assignment_id`, `execution_id`, `assessment_id`, `remediation_work_id`,
`memory_id`.

Idempotent: second run reuses the same stable scenario identities.
Console prints `created` / `reused` / `updated` per step.

Synthetic identities (from `teacher_os_review_scenario.py`):

- Tenant: `71b5fb49-2bdb-56c3-ab7c-3b33e92a89f0`
- Principal: `f85329ab-f05b-564e-a67b-318f3e1f3cf3`

The loader upserts that principal as **ACTIVE HUMAN** via the bootstrap role
(runtime must not classify / INSERT principals).

## 4. Start backend (development Teacher OS app + Fake gateway)

F5 local API uses different local-dev tenant/principal IDs and may not compose
a Model Gateway. For this demo, serve the development Teacher OS app bound to
the **synthetic** teacher with a FakeStructuredModelGateway (default when no
OpenAI env is set):

```powershell
# Set runtime URL from env — no credentials in docs
$env:AIEOS_RUNTIME_DATABASE_URL = "<runtime-sqlalchemy-url-from-env>"

uv run python -c @"
from sqlalchemy import create_engine
import uvicorn
from aieos.development.app_factory import build_development_teacher_os_app
from aieos.development.teacher_os_review_scenario import (
    SYNTHETIC_PRINCIPAL_ID, SYNTHETIC_TENANT_ID,
)
from aieos.platform.ai.fake import FakeStructuredModelGateway
from aieos.domains.education.worksheet_v1 import WorksheetV1
from aieos.domains.education.preparation_kit_v1 import PreparationKitV1
from aieos.domains.teaching.application.assistant_answer_v1 import TeacherAssistantAnswerV1
from tests.domains.teaching.worksheet_fixtures import valid_worksheet_model
from tests.domains.teaching.helpers_dev04_i06 import pass_preparation_kit
import os

def factory(req):
    t = req.output_type
    if t is WorksheetV1: return valid_worksheet_model()
    if t is PreparationKitV1: return pass_preparation_kit()
    if t is TeacherAssistantAnswerV1: return TeacherAssistantAnswerV1.development_fake(req.input_text)
    raise RuntimeError(t)

engine = create_engine(os.environ['AIEOS_RUNTIME_DATABASE_URL'])
app = build_development_teacher_os_app(
    engine,
    tenant_id=SYNTHETIC_TENANT_ID,
    principal_id=SYNTHETIC_PRINCIPAL_ID,
    model_gateway=FakeStructuredModelGateway(result_factory=factory),
    ai_provider_id='fake',
    ai_model_id='fake-model',
)
uvicorn.run(app, host='127.0.0.1', port=8000)
"@
```

API: http://127.0.0.1:8000

Point the frontend proxy at this port when verifying the demo:

```powershell
$env:VITE_DEV_API_PROXY_TARGET = "http://127.0.0.1:8000"
```

## 5. Start frontend

From `eduvijna-aieos-frontend`:

```powershell
pnpm install
pnpm run dev
```

UI: http://localhost:5173

## 6. Auth synthetic teacher

Open the DEV session panel and connect with:

| Field | Value |
|-------|-------|
| API origin | `http://127.0.0.1:8000` (or leave blank if Vite proxies `/api`) |
| Tenant | `71b5fb49-2bdb-56c3-ab7c-3b33e92a89f0` |
| Bearer | any non-empty token (development authenticator is fixed-principal) |

Tenant must match the synthetic ID from `tmp/teacher-os-demo.json`.

## 7. Visit Teacher OS screens

Smoke the seeded journey:

1. Today’s Mission
2. Work / **Review** (pending IN_REVIEW worksheet) / **Library** (separate published worksheet)
3. Teach (assignment + execution on published work / `class-5a`)
4. Assess / Improve (MIXED assessment + remediation work)
5. Memory preferences

## 8. Verify Assistant with Fake gateway

`POST /api/v1/teacher-os/assistant` (`teacher_os_assistant_respond`).

Example questions the Fake `TeacherAssistantAnswerV1.development_fake` answers
deterministically:

- What should I focus on today?
- Summarize the current teaching work.
- What preparation is already available?
- Why does this class need remediation?
- How should I adjust tomorrow's lesson?

Expect suggestions only — no Memory / Publish / Assign / Teach / Assess /
remediation side effects.

## 9. Optional real provider (backend env only)

`build_development_teacher_os_app` may use OpenAI when provider config is
present in **backend process env**. Do not put provider secrets in frontend
`VITE_*` variables or docs. Omit Fake gateway wiring only when you intentionally
want the env-backed provider path.

## Authority reminders

- Assistant never trusts client context snapshots; `teaching_work_id` is
  re-read and re-authorized server-side.
- HUMAN ACTIVE principal required; WORKLOAD / NULL kind / inactive fail closed.
- Classification SoR failures map to `authorization_unavailable` (503).
