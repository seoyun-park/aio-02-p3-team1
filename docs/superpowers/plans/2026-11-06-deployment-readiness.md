# Deployment readiness implementation plan

**Goal:** Prepare the frozen service and admin dev revisions for EC2 deployment.
**Architecture:** Keep the existing applications, share PostgreSQL 18 in one Compose project, serve static frontends through Nginx, and protect admin with HTTP Basic authentication at its reverse proxy. Inject the admin API key from a mounted server secret.
**Constraints:** Coverage stays at 70%; preserve existing worktrees and DB data. EC2 startup, migrations and Nginx reload require the user's approval.

- [x] Run backend tests against an isolated PostgreSQL 18; cover untested recovery behavior with assertions on durable results.
- [x] Apply migrations 002–008 in CI. Provide an explicit migration command for a fresh production DB, never on application startup.
- [x] Complete production Compose: managed DB volume, loopback 8080, worker healthcheck and monitor spool.
- [x] Build admin frontend with same-origin API calls; retain local demo mode only for development. Protect every admin request with Basic authentication and inject the upstream key server-side.
- [x] Add admin CI/build/ECR workflow and production Compose overlay using exact image tags.
- [x] Verify backend coverage, both frontend builds/tests, Compose model and admin unauthorized/authorized requests.
- [x] Record release revisions, required external settings and approval commands.
