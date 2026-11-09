# EC2 Current-State Adjustment Plan

> **For agentic workers:** Execute this plan inline and preserve existing EC2 services. Do not run deployment steps.

**Goal:** Align the production Compose template and host Nginx example with the user-confirmed fresh EC2 state.

**Architecture:** Keep the existing host Nginx on port 80 and bind only the frontend container to loopback port 8080. Route host Nginx traffic to the frontend container; its existing internal Nginx proxies `/api/` to FastAPI on the Docker network. Let Compose create one explicitly named PostgreSQL 18 volume for the new empty database; tune PostgreSQL memory conservatively and leave app limits unpinned until runtime measurements exist.

**Tech Stack:** Docker Compose, PostgreSQL 18, Nginx, GitHub Actions, AWS ECR.

**Spec:** `docs/PRD.md` (PostgreSQL 18, Docker Compose, restart policy) and the user-confirmed EC2 snapshot dated 2026-11-06.

## Global Constraints

- Do not execute commands on EC2 or change Nginx, containers, volumes, or DB state before explicit approval.
- Do not run migrations or start local containers during configuration validation.
- Keep `.env.prod` secrets out of the repository and logs.
- Preserve one backend and one worker process; avoid replicas on the 2 GiB host.
- Use only exact SHA-tagged ECR images; require a fresh frontend image after the Kakao key is configured.

---

### Task 1: Correct production Compose and environment template

**Files:** Modify `compose.prod.yaml` and `.env.prod.example`.

- [x] Replace the nonexistent external DB volume requirement with Compose-created volume `seoul-wave-prod-postgres-data` mounted at `/var/lib/postgresql`.
- [x] Tune PostgreSQL 18 for the small host using `shared_buffers=128MB`, `max_connections=30`, `work_mem=4MB`, and `maintenance_work_mem=64MB`.
- [x] Bind frontend on `127.0.0.1:8080`; keep backend and PostgreSQL private on Docker networks.
- [x] Remove `POSTGRES_VOLUME_NAME`; set fresh-database name/user examples and require a newly generated password.

### Task 2: Prepare host Nginx routing and update operational guidance

**Files:** Create `docs/operations/nginx-ec2.conf.example`; modify `docs/operations/ec2-deployment.md`.

- [x] Route host traffic to `127.0.0.1:8080`; preserve the frontend Nginx's existing internal `/api/` to `backend:8000` proxy.
- [x] Replace stale EC2 and ECR observations with the user-confirmed snapshot and successful post-merge image build result.
- [x] State that the new DB is empty and requires a separately approved schema/data initialization; forbid `docker compose down -v`.
- [x] Record the HTTP-only Secure-cookie limitation and keep deployment gated on TLS or an explicit demo policy decision.
- [x] Document the memory tuning rationale, no-swap condition, no arbitrary hard caps, and post-start measurement/stop criteria.

### Task 3: Validate without changing services

**Files:** Validate the modified Compose and Nginx example.

- [x] Run `docker compose ... config --quiet` with non-secret placeholders only; do not run `up`, migrations, or Nginx reload.
- [x] Check the Nginx example with a local syntax validator if available; validator is unavailable in this Windows workspace, so route/proxy syntax was manually reviewed and the limitation recorded.
- [x] Inspect the final diff and confirm no backend business logic, production server, or database state was changed.
