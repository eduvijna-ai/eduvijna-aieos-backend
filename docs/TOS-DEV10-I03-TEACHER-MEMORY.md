# TOS-DEV10-I03 — Teacher Memory v1

Teacher Memory is the durable teacher-owned preference profile System of Record
for Teacher OS. Durable ownership is `tenant_id` + represented/effective HUMAN
teacher Principal (`teacher_principal_id`), resolved by
`resolve_represented_teacher_principal` from trusted server-side identity.

`TrustedSecurityContext.principal_id` is the calling/authenticated Principal.
It is **not** definitionally the Memory owner. Direct Teacher OS requests where
`principal_id == effective_actor_id` resolve owner to that Principal only as an
explicit direct-execution fallback. Clients never supply `teacher_principal_id`
or `tenant_id` on Memory write/read bodies.

## Authority boundaries

- Memory stores an explicit, closed preference vocabulary (`schema_version = 1`).
- GET `/api/v1/teacher-os/memory` never creates a profile (404 when absent).
- POST creates once per teacher; duplicate create is deterministic and does not
  rewrite preferences.
- PUT replaces preferences under `If-Match` aggregate revision concurrency.
- Non-API / service / workload execution without a safely represented HUMAN
  teacher fails closed and cannot become Memory owner.
- Continuous Context, chat history, learner data, and preference inference are
  out of scope for this increment.

## Prepare decision — MEMORY → GENERATION CONTEXT DEFERRED

Prepare may read Memory later as **UI defaults only**. Feeding Memory preferences
into AI generation prompts would require an ADR-052 generation-context contract
change and is intentionally deferred. Current Prepare application code does not
import or bind Teacher Memory.

## Migration

- Alembic revision: `tosd100001` (revises `tosd090002`)
- Table: `teaching.teacher_memories` with tenant RLS, unique teacher key, no DELETE
