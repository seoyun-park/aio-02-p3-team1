# EC2 Production Compose Implementation Plan

> **For agentic workers:** Execute this plan task by task and preserve the user’s existing files and services.

**Goal:** Prepare a separate, reviewable EC2 Compose configuration that runs the verified ECR images with PostgreSQL 18 while preserving the existing database volume.

**Architecture:** Keep development `compose.yaml` unchanged. Add `compose.prod.yaml` that consumes exact ECR image references, requires an already-existing external PostgreSQL volume, keeps database/API ports private, and runs the itinerary worker from the same backend image. Add the Kakao JavaScript key as a frontend Docker build argument because Vite embeds `VITE_` values during build. Document the approval-gated EC2 procedure without running it.

**Tech Stack:** Docker Compose, PostgreSQL 18, FastAPI, React/Vite, GitHub Actions OIDC, AWS ECR.

**Spec:** User-provided EC2 deployment instructions; `docs/PRD.md` PostgreSQL 18, Docker Compose, D-13 restart policy, and deployment safety requirements.

## Global Constraints

- PostgreSQL image remains major version 18 and its persistent mount is `/var/lib/postgresql`.
- Use only exact SHA-tagged ECR images; do not use `latest`.
- Require the existing EC2 volume by external name; never create or replace a database volume implicitly.
- Do not run production migrations or `docker compose up` before approval.
- Do not expose PostgreSQL 5432 or FastAPI 8000 on the EC2 host.
- Do not commit `.env.prod` or any real passwords/API keys/session secrets.
- Keep the development `compose.yaml` behavior unchanged.

---

### Task 1: Add separate production Compose and safe environment template

**Files:**
- Create: `compose.prod.yaml`
- Create: `.env.prod.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes exact `FRONTEND_IMAGE` and `BACKEND_IMAGE` ECR references plus explicit production environment variables.
- Requires `POSTGRES_VOLUME_NAME` to identify an existing external EC2 volume.
- Exposes only the frontend host port; backend, worker, and PostgreSQL communicate over Compose networks.

- [x] Define PostgreSQL 18, private database networking, a DB health check, `restart: unless-stopped`, and an external named volume.
- [x] Define backend and worker from the same backend ECR image with DB/API settings; check backend DB readiness and use restart supervision for the worker (no worker health endpoint exists).
- [x] Define frontend from its ECR image with a configurable host bind/port and static-server health check.
- [x] Add a shareable `.env.prod.example` with names and explanations only; allow this sample through `.gitignore` while keeping `.env.prod` ignored.
- [x] Validate rendered Compose syntax using dummy values only; do not start containers.

### Task 2: Pass the Kakao JavaScript key into the production frontend image build

**Files:**
- Modify: `frontend/Dockerfile.prod`
- Modify: `.github/workflows/frontend-ci.yml`

**Interfaces:**
- Consumes the GitHub repository variable `VITE_KAKAO_MAP_JS_KEY`.
- Produces a static frontend image whose Vite bundle can load the Kakao Maps SDK when the variable is set.

- [x] Declare and export the Docker build argument before `npm run build`.
- [x] Pass the repository variable to `docker build` without printing it in workflow logs.
- [x] Keep the key optional in CI; if absent, the existing UI fallback remains and the workflow reports a warning.
- [x] Run the existing frontend tests and production build (tests/build passed with a one-off Node Windows user-info shim and Vite's runner config loader due sandbox host restrictions).

### Task 3: Document EC2 preflight, deployment gate, and rollback procedure

**Files:**
- Create: `docs/operations/ec2-deployment.md`
- Modify: `README.md`

**Interfaces:**
- Documents read-only checks for EC2 state, Docker, current containers/volumes, port 80, IAM ECR pull access, and exact image tags.
- Documents migration review and backup prerequisites without executing migrations.
- Separates the prepared Compose configuration from an approved live rollout.

- [x] Record currently verified image SHAs and identify the missing AWS live-state checks.
- [x] Give non-destructive preflight, config validation, image pull, deploy, health-check, and rollback steps.
- [x] State that production DB migrations require schema inspection, backup, and explicit approval because current SQL migrations have no ledger and are not idempotent.
- [x] Link the operations guide from README without changing development Compose instructions.

### Task 4: Validate prepared files and report the rollout gate

**Files:**
- Validate: `compose.prod.yaml`, `.env.prod.example`, frontend build workflow, and operations guide.

- [x] Run Compose config validation using non-secret dummy values.
- [x] Run the existing frontend test and build commands.
- [x] Review the final diff and confirm no development Compose, backend business logic, or production database state was changed.
- [x] Report that AWS CLI and Docker Engine access failures prevented live EC2/volume inspection; leave live deployment and migrations approval-gated.
