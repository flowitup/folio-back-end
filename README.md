# Folio — Backend (API)

The server side of **Folio**, a Construction Management System that helps small and mid-sized construction companies keep track of their projects, crews, hours worked, and invoices in one place.

This repository contains the API. The web app you actually click on is in [folio-front-end](https://github.com/flowitup/folio-front-end) and the iOS/Android app in [folio-mobile-app](https://github.com/flowitup/folio-mobile-app) — they are designed to run together.

---

## What you can do with Folio

Folio is built around the day-to-day reality of running construction work:

- **Projects** — Create a project by its site address (a crew identifies a jobsite by where it is, not what it's called — naming it is optional), give it a team, and switch between projects in the app.
- **Team & roles** — Bring people into a company with its reusable join code, by adding them by phone, or by importing members from another company; invite someone outside the company to a project by email. Each member holds a company-wide role — `admin`, `manager` or `member` — that, together with their project assignments and any per-member grants or denies an admin sets, decides what they can see and do.
- **Labor tracking** — Keep a list of workers, their daily rate, phone number and job role. Log who showed up each day — full-day, half-day or overtime shifts — plus extra "supplement" hours that are banked and paid as bonus days: every 8 hours make a day, 4 or more left over make a half day.
- **Labor cost summary** — See per-worker totals, including priced cost and bonus cost shown separately so nothing is hidden inside one big number.
- **Excel & PDF exports** — Export labor reports for any 1-to-24-month window, either for the whole project or for a single worker. Excel uses French-format currency; PDFs render Vietnamese accents correctly.
- **Invoices** — The project ledger: funds released to the project (split into a company purse and a personal purse by how they were paid) and what the project spent — labor, materials & services, other costs and returns — each with line items, totals and attachments. A company cash advance (company money handed to someone to pay site costs) is not a release: it is listed under other costs and shown as spent from the company purse.
- **Billing documents** — Draft a devis (quote) for a client, mark it sent once you have delivered it, then accepted, rejected or expired; convert it straight into a facture and follow that to paid, overdue or cancelled; export either as a PDF or Excel.
- **Planning & chat** — A per-project Kanban board for tasks, plus optional team chat channels for deployments that turn `FEATURE_CHAT` on.
- **Notes** — A per-project build journal: short entries with a title, description and category (inspection, delivery, payment, decision, call, general), each marked open or done.
- **Notifications** — In-app alerts in the bell-icon dropdown (attendance waiting for validation, new company members for admins), plus optional push notifications for chat, tasks, membership, attendance and billing events.

---

## Running the API

The API is the engine behind the web and mobile apps. You only need to run it directly if you are setting up a local environment, integrating another system with it, or hosting your own copy.

### Easiest way — Docker

If Docker is installed, this starts the API, the background worker (used only by the assistant), PostgreSQL and Redis, then creates the database schema:

```bash
docker compose up -d
docker compose exec api flask db upgrade   # first run, and after every update
```

The API listens on **http://localhost:5000**. The web app (separate repo) connects to it on that address.

To create a first account, sign up from the mobile app, or seed an admin (plus a demo company) with a French phone number:

```bash
docker compose exec -e ADMIN_EMAIL=you@example.com -e ADMIN_PHONE=+33612345678 api python -m scripts.seed --with-admin
```

Sign-in codes are not texted locally: with the default `SMS_PROVIDER=log` they are written to the API log (`docker compose logs api`). File uploads (attachments, documents, photos, chat images) also need an S3-compatible store such as MinIO at `S3_ENDPOINT_URL`; this compose file does not start one.

To stop everything:

```bash
docker compose down
```

### Manual setup (Python)

If you'd rather run it on your own machine without Docker:

1. Install [uv](https://docs.astral.sh/uv/) (a fast Python package manager).
2. Install Python 3.12 and the project dependencies:

   ```bash
   uv python install 3.12
   uv sync
   ```

3. Copy the environment template and fill in your values:

   ```bash
   cp .env.example .env
   ```

4. Have PostgreSQL and Redis running and point `DATABASE_URL` / `REDIS_URL` at them (for example `docker compose up -d db redis`, which matches the template's defaults), then create the schema:

   ```bash
   uv run flask db upgrade
   ```

   Video thumbnails need `ffmpeg` and the assistant's PDF reading needs `poppler-utils` on the machine (both are in the Docker image).

5. Start the API:

   ```bash
   uv run flask run
   ```

6. Only if you turn the assistant on (`FEATURE_ASSISTANT=1`): start the background worker in a second terminal. It runs the assistant's jobs from the `assistant` RQ queue; everything else happens inside the API process (invitation emails are sent inline, exports are built on request, notifications are computed when the app asks for them).

   ```bash
   uv run python -m stack.queue.rq_worker
   ```

A quick health check:

```bash
curl http://localhost:5000/health
# {"status": "ok"}
```

To seed a first admin without Docker: `ADMIN_EMAIL=you@example.com ADMIN_PHONE=+33612345678 uv run python -m scripts.seed --with-admin`.

### Tests & linting

The same gates CI runs on every pull request (see `.github/workflows/ci.yml`):

```bash
uv sync --frozen --extra dev
python3 scripts/check_migration_heads.py   # fails when Alembic has more than one head
uv run ruff check .
uv run black --check .
uv run mypy .      # report-only in CI — does not fail the build
uv run pytest
```

### Configuration

The most useful settings, configured through environment variables:

| Variable | What it controls |
|---|---|
| `DATABASE_URL` | Where Folio stores its data — PostgreSQL (the migrations use PostgreSQL-only SQL; SQLite is only used by the test suite). |
| `SECRET_KEY` / `JWT_SECRET_KEY` | Flask session signing / sign-in token signing. Production refuses to boot while either is empty or still contains `dev-`; set long random values. |
| `REDIS_URL` | Rate limits, the sign-out token blocklist (without Redis it falls back to per-process memory, so a signed-out token stays valid on other workers and after a restart) and the assistant's job queue, hourly limit and daily cost cap. |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | S3-compatible store for every uploaded file. Production refuses to boot on the MinIO default keys or a localhost endpoint. |
| `CORS_ORIGINS` | Comma-separated origins allowed to call the API with credentials (browser CORS). Defaults to `http://localhost:3000`. |
| `TRUSTED_PROXY_HOPS` | How many reverse proxies may name the caller through `X-Forwarded-For`. Rate limits are keyed on the caller's address, so a deployment behind a reverse proxy or tunnel must set it to the number of proxies in front of the API (e.g. `1` behind a single one), or everyone shares one bucket. Default `0` trusts nothing. |
| `EMAIL_PROVIDER` | Outgoing email — project invitations and "added to project(s)" notices, sent directly by the API process. Only `resend` (needs `RESEND_API_KEY` and `FROM_EMAIL`; the API will not start if either is empty) and `inmemory` (tests) are implemented. With any other value — including the unset default, `smtp` — no email adapter is wired: invitations are still created but their emails are skipped (an error is logged), so the invitee never receives the link. Email is a contact field only — it is never a sign-in credential. |
| `SMS_PROVIDER` | How sign-in codes go out: `log` (API log, dev), `twilio` (`TWILIO_*`) or `gateway` — an "SMS Gateway for Android" endpoint (`SMS_GATEWAY_URL`, `SMS_GATEWAY_USERNAME`, `SMS_GATEWAY_PASSWORD`). Phone + SMS code is the only way to sign in, and sign-in, sign-up and invitation acceptance accept French numbers only (`+33…` / `0…`). |
| `OTP_TEST_CODE` | **Must stay unset in production.** A fixed 6-digit sign-in code accepted in place of the real SMS one, so automated tests can complete a phone sign-in without reading a message. It is refused unless `FLASK_ENV` is explicitly `development` or `testing` — for this check, an unset, empty or unrecognised value counts as production — and it still requires a code to have been requested for that number. |
| `OTP_REVIEWER_PHONE` / `OTP_REVIEWER_CODE` | Store-review account (App Store / Play review). **Set both or neither.** One French phone number whose sign-in code is always `OTP_REVIEWER_CODE` (6 digits): `/otp/request` for that number stores a code but sends no SMS and skips the throttle, and `/otp/verify` accepts the fixed code for that number only — every other phone still needs its real SMS code, and the fixed code is rejected for them. Meant for production while an app version is under review; unset both afterwards. The number must exist as an active user (sign it up once through the app: the sign-up code follows the same rule). |
| `REFRESH_TOKEN_POLICY` | `expiring` (default — 7-day refresh token) or `persistent` (never expires; the session lasts until sign-out). |
| `EXPOSE_DOCS` | Set to `1` to serve the OpenAPI spec and Swagger UI (see below) when `FLASK_ENV=production`; with any other `FLASK_ENV` they are always served. |

A full template lives in `.env.example`.

---

## Assistant (AI layer)

Folio Assistant answers inside team chat: mention `@folio` in a company or project channel (or reply to one of its messages) to identify a material photo, import a ticket/receipt into an invoice, fetch an invoice from a merchant's website, find or move equipment, check the day's roster, log or validate attendance, create or list tasks, and answer questions about a site. Each company also has an admin channel for its admins, the only place where the assistant answers confidential questions (project income, salaries, unpaid client invoices, who asked it what). It needs team chat (`FEATURE_CHAT=1`) and ships **dark** until `FEATURE_ASSISTANT=1` and the two core provider keys are set.

| Variable | What it controls |
|---|---|
| `FEATURE_ASSISTANT` | Master switch, off by default. When off, `@folio` messages are not handed to the assistant, `POST /assistant/actions` and `GET /assistant/audit` answer 404, jobs already queued do nothing when they run, and the `ai-browser` poller idles — a real kill switch, not just a UI toggle. |
| `DEEPSEEK_API_KEY` | Vision + chat text (photo reading, scan generation verification, chit-chat); also drives the `ai-browser` agent on merchant sites. Required, with `TYPESAFE_API_KEY`, for the assistant to be considered "enabled". |
| `TYPESAFE_API_KEY` | Jev (TypeSafe AI) — every routing/gating decision (`system_one`), never free text. |
| `GEMINI_API_KEY` | Scan generation when `SCAN_MODE=genai` (a clean redraw of a receipt photo). |
| `SCAN_MODE` | `genai` (default — Gemini redraws the receipt, the redraw is re-read and checked for faithfulness, falls back to `opencv` automatically) or `opencv` (local perspective-correct + threshold; asks DeepSeek for the page corners only when OpenCV cannot find the document outline — no image generation). |
| `ASSISTANT_DAILY_COST_CAP_USD` | Daily USD spend cap across every provider; once reached, the pipeline answers a quota template instead of calling anything. A rolling per-user hourly rate limit (30 runs/hour) applies independently. |
| `JOB_OFFPEAK_ONLY` | Read by the `ai-browser` container: only run merchant-site jobs after noon Europe/Paris. |
| `BROWSER_CHROME_PATH` / `BROWSER_PROFILE_DIR` / `BROWSER_DOWNLOADS_DIR` | Read directly by the `ai-browser` container process (not through the app's `Config` class): its Chrome binary and persisted profile/downloads paths. |

A missing DeepSeek or TypeSafe key gets a "not configured" reply instead of a 500; a missing Gemini key silently falls back to the OpenCV scan. The separate `ai-browser` container checks the same `FEATURE_ASSISTANT`/`DEEPSEEK_API_KEY`, plus a presence-only `TYPESAFE_API_KEY_CONFIGURED` flag (never the real TypeSafe key, which that container never calls), so it goes dark in lockstep with the web process.

Invoice-fetch (`fetch_invoice`) and material product search (`find_product`) jobs both run on the shared RQ `assistant` queue (`stack.queue.rq_worker`) plus a dedicated **`ai-browser`** container (`Dockerfile.browser`, `docker/browser-entrypoint.sh`) that polls `assistant_jobs` directly with plain SQL — it never boots the Flask app.

To sign into each merchant site once by hand, start the `ai-browser` image with the `login-session` argument (its entrypoint is `docker/browser-entrypoint.sh`) on the same profile volume the poller uses: it runs Chrome on that persistent profile behind Xvfb + x11vnc so you can log in over VNC. x11vnc runs **without a password** and listens on every interface inside the container — publish its port (5900) on loopback only and reach it through an SSH tunnel. The default poll-loop mode never logs in by itself (hard rule: it only browses sessions that are already authenticated).

Material photo → library does not call a web-search API: once DeepSeek identifies the photo, the same browser agent that fetches invoices searches the allow-listed merchant sites' own search pages for a matching product page, and Jev picks the best candidate (or "none", which falls back to a photo-only library entry the user completes later).

Accuracy against a hand-labelled gold set (S1 invoice extraction, A1 material ID):

```bash
uv run python -m scripts.ai_eval.run_eval --invoices eval/invoices --materials eval/materials
```

See `eval/README.md` for the gold format — this repo ships no real invoices/photos, only the harness and two example gold files.

---

## What's inside the API

Business endpoints live under `/api/v1/` (the health check is `/health`; interactive docs below), grouped by area:

| Area | Endpoints |
|---|---|
| **Authentication** | Public sign-in options (`GET /auth/config`), phone (French numbers) + SMS sign-in and sign-up, sign out, refresh, "who am I", profile update, self-service account deletion |
| **Projects** | List, create, view, update, delete; assign/unassign members |
| **Project invitations** | Invite someone to a project by email, verify/accept, revoke, list |
| **Companies** | Create/manage companies, attach or detach a member, join codes, primary company, per-member role and custom permission grants/denies |
| **Persons** | Person records that link a worker's identity across projects and companies: typeahead search (limited to companies where you are admin or manager), create, merge duplicates (platform ops only) |
| **Admin** | Platform-ops: bulk-add a user to many projects, search and edit user profiles |
| **API keys** | Personal API keys for headless automation |
| **Workers** | Add, edit, deactivate workers in a project; dated daily-rate changes |
| **Labor entries** | Log attendance (one at a time or in bulk) and supplement hours; worker self-log with manager validation of new days and edits; day roster; cross-project conflict check; per-worker and monthly cost summaries; daily activity log and day descriptions |
| **Labor roles** | Company job-title catalogue (e.g. mason, electrician) assigned to workers |
| **Labor exports** | Excel or PDF for the project or a single worker |
| **Invoices** | Project ledger — released funds (company and personal purses), labor, materials & services, others (incl. company cash advances), returns — create, list, view, edit, delete, attach files, export; labor payments per month; company-wide refund tracking for materials & services expenses |
| **Billing documents** | Devis (quotes) and factures for clients: create, clone, convert devis to facture, status workflow, PDF/XLSX render, templates, import |
| **Chiffrage** | Break a project into sections, list the articles needed, compare store quotes per article |
| **Payment methods** | Company-scoped list of the ways expenses get paid (e.g. cash, the company account, someone's own card), each flagged as paid by the company or paid personally — which decides whose purse an expense counts against |
| **Planning** | Per-project Kanban tasks |
| **Notes** | Per-project journal entries by category: create, list, edit (including open/done), delete |
| **Notifications** | Attendance waiting for validation, new company members (for admins) and reminders left on older dated notes (list, dismiss); push-notification category preferences |
| **Push** | Register/unregister a device's push token |
| **Chat** | Team chat channels, messages and attachments (`FEATURE_CHAT`) |
| **Assistant** | Answer an assistant choice message; supervision audit log (`FEATURE_ASSISTANT`) |
| **Features** | Which optional features (chat, assistant) this deployment has on |
| **Project documents** | Upload, list, download, rename, delete, tag files |
| **Project analyses** | Project-scoped library of uploaded HTML reports, with tags |
| **Project photos** | Upload, list, thumbnail, edit, delete site photos and videos |
| **Library (bibliotheque)** | Company product catalogue: suppliers, categories, products, import, images |
| **Inventory** | Company equipment: warehouses (with address) and rows of tools with quantity, working / damaged, and where they are (warehouse or site) |

Interactive docs: the OpenAPI spec is served at `/openapi.json` and Swagger UI at `/v1/documentation` — always, unless `FLASK_ENV=production`, where they need `EXPOSE_DOCS=1`.

Sign-in is JWT-based. Browsers use HTTP-only cookies (with CSRF protection); other clients can use a `Bearer` token in the `Authorization` header. A personal API key (`folio_sk_...`, via `Authorization: Bearer` or `X-API-Key`) works the same way: it carries its owner's full permissions, has no scope and never expires (revoke it to end access). It can read but not change anything under `/auth` (profile, phone, sign-out, account deletion) or the platform-admin `/admin` endpoints, and cannot use `/api-keys` at all.

---

## Security at a glance

- Requesting or verifying a sign-in code is limited to 5 per minute per client address; each phone also gets a 60-second resend cooldown, at most 5 codes an hour and 5 wrong guesses per code (`OTP_*` settings). Access tokens are signed and expire after 30 minutes; refresh tokens expire after 7 days, or only at sign-out with `REFRESH_TOKEN_POLICY=persistent`.
- Browser sessions are CSRF-protected.
- Access to projects and company data is decided on the server from the caller's company role, project assignments and per-member grants — never by the app.
- API keys never expire and carry their owner's full permissions (a company admin's key can still manage that company's members, roles, grants and projects). A key cannot change anything under `/auth` — so it cannot rewrite the sign-in phone — cannot mutate the platform-admin endpoints, and cannot list, create or revoke API keys.
- Bulk admin operations (e.g. adding a user to many projects at once) are rate-limited per user and per IP.
- Exported PDFs sanitize user-entered text to prevent markup injection.

---

## Support

- Bug reports and feature requests: [open an issue](https://github.com/flowitup/folio-back-end/issues).
- Operational issues (deployment, database, email): contact your administrator.
