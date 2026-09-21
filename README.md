# Folio — Backend (API)

The server side of **Folio**, a Construction Management System that helps small and mid-sized construction companies keep track of their projects, crews, hours worked, and invoices in one place.

This repository contains the API. The web app you actually click on is in the [folio-front-end](../folio-front-end) repository — they are designed to run together.

---

## What you can do with Folio

Folio is built around the day-to-day reality of running construction work:

- **Projects** — Create projects (e.g. "Downtown Office Tower", "Riverside Apartments"), give each one its own address and team, and switch between them from the top bar.
- **Team & roles** — Invite people to a project by email. Each member gets a role (owner, manager, foreman, accountant, viewer) that decides what they can see and do.
- **Labor tracking** — Keep a list of workers, their daily rate, and phone number. Log who showed up each day, full-day or half-day shifts, plus extra "supplement" hours that are automatically converted into bonus days at month-end.
- **Labor cost summary** — See per-worker totals, including priced cost and bonus cost shown separately so nothing is hidden inside one big number.
- **Excel & PDF exports** — Export labor reports for any 1-to-24-month window, either for the whole project or for a single worker. Excel uses French-format currency; PDFs render Vietnamese accents correctly.
- **Invoices** — Issue Client, Labor, and Supplier invoices. Each one has line items, totals, and a clean print-ready view.
- **Notes & reminders** — Post notes on a project with a due date. Members get a reminder in the bell-icon dropdown when the lead time hits.
- **Notifications** — In-app notifications for invitations, reminders, and project events.

A more visual walkthrough of every screen lives in [`FEATURES.md`](../FEATURES.md) at the root of the repo.

---

## Running the API

The API is the engine behind the web app. You only need to run it directly if you are setting up a local environment, integrating another system with it, or hosting your own copy.

### Easiest way — Docker

If Docker is installed, one command starts everything (API, background worker, database, Redis):

```bash
docker compose up -d
```

The API listens on **http://localhost:5000**. The front-end (separate repo) connects to it on that address.

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

4. Start the API:

   ```bash
   uv run flask run
   ```

5. Start the background worker (handles emails, exports, reminders) in a second terminal:

   ```bash
   uv run python -m stack.queue.rq_worker
   ```

A quick health check:

```bash
curl http://localhost:5000/health
# {"status": "ok"}
```

### Configuration

The most useful settings, configured through environment variables:

| Variable | What it controls |
|---|---|
| `DATABASE_URL` | Where Folio stores its data. PostgreSQL recommended for production. |
| `SECRET_KEY` / `JWT_SECRET_KEY` | Sign-in tokens. Set unique values in production. |
| `REDIS_URL` | Used for background jobs and rate-limiting. |
| `TRUSTED_PROXY_HOPS` | How many reverse proxies may name the caller through `X-Forwarded-For`. Rate limits are keyed on the caller's address, so a deployment behind a proxy needs this set (production: `1`, for cloudflared) or everyone shares one bucket. Default `0` trusts nothing. |
| `EMAIL_PROVIDER`, `SMTP_*` | Outgoing email — invitations and reminders. Email is a contact field only; it is never a sign-in credential. |
| `SMS_PROVIDER` | How sign-in codes go out: `log` (API log, dev), `twilio` (`TWILIO_*`) or `gateway` — an "SMS Gateway for Android" endpoint (`SMS_GATEWAY_URL`, `SMS_GATEWAY_USERNAME`, `SMS_GATEWAY_PASSWORD`). Phone + SMS code is the only way to sign in. |
| `OTP_TEST_CODE` | **Must stay unset in production.** A fixed sign-in code accepted in place of the real SMS one, so automated tests can complete a phone sign-in without reading a message. It is refused unless `FLASK_ENV` is explicitly `development` or `testing` — an unset, empty or unrecognised value counts as production — and it still requires a code to have been requested for that number. |
| `OTP_REVIEWER_PHONE` / `OTP_REVIEWER_CODE` | Store-review account (App Store / Play review). **Set both or neither.** One French phone number whose sign-in code is always `OTP_REVIEWER_CODE` (6 digits): `/otp/request` for that number stores a code but sends no SMS and skips the throttle, and `/otp/verify` accepts the fixed code for that number only — every other phone still needs its real SMS code, and the fixed code is rejected for them. Meant for production while an app version is under review; unset both afterwards. The number must exist as an active user (sign it up once through the app: the sign-up code follows the same rule). |

A full template lives in `.env.example`.

---

## Assistant (AI layer)

Folio Assistant is a pinned per-user AI conversation inside team chat: identify a
material photo, import a ticket/receipt into an invoice, fetch an invoice from a
merchant's website, find/move inventory, and answer chantier questions. It ships **dark**
until both the feature flag and the two core provider keys are set.

| Variable | What it controls |
|---|---|
| `FEATURE_ASSISTANT` | Master switch. Off (default) hard-404s the `assistant:<user>` channel and its actions endpoint — a real kill switch, not just a UI toggle. |
| `DEEPSEEK_API_KEY` | Vision + chat text (photo reading, scan generation verification, chit-chat). Required, with `TYPESAFE_API_KEY`, for the assistant to be considered "enabled". |
| `TYPESAFE_API_KEY` | Jev (TypeSafe AI) — every routing/gating decision (`system_one`), never free text. |
| `GEMINI_API_KEY` | Scan generation when `SCAN_MODE=genai` (a clean redraw of a receipt photo). |
| `SCAN_MODE` | `genai` (Gemini redraw + faithfulness check, falls back automatically) or `opencv` (perspective-correct + threshold, no extra API call). |
| `JOB_OFFPEAK_ONLY` | Restrict the browser-worker's merchant-site jobs to after noon Europe/Paris (owner runbook). |
| `ASSISTANT_DAILY_COST_CAP_USD` | Daily USD spend cap across every provider; once reached, the pipeline answers a quota template instead of calling anything. A rolling per-user hourly rate limit (30 runs/hour) applies independently. |
| `BROWSER_WORKER_CHROME_PATH` / `_PROFILE_DIR` / `_DOWNLOADS_DIR` | The `ai-browser` container's Chrome binary and persisted profile/downloads paths. |

Every provider adapter degrades to a "not configured" reply when its key is empty — a
half-configured deployment stays dark instead of 500ing.

Invoice-fetch (`fetch_invoice`) and material product search (`find_product`) jobs both
run on the shared RQ `assistant` queue (`stack.queue.rq_worker`) plus a dedicated
**`ai-browser`** container (`Dockerfile.browser`, `docker/browser-entrypoint.sh`) that
polls `assistant_jobs` directly with plain SQL — it never boots the Flask app. Run
`docker/browser-entrypoint.sh login-session` once to sign into each merchant site
manually (noVNC/SSH-tunnel only, see [`../docs/assistant-merchant-login.md`](../docs/assistant-merchant-login.md)); the container's
poll-loop mode never logs in itself (hard rule: read-only browsing of already-authenticated
sessions).

Feature A (material photo -> library) no longer calls a web-search API: once DeepSeek
identifies the photo, the same browser agent that fetches invoices searches the
allow-listed merchant sites' own search pages for a matching product page, and Jev picks
the best candidate (or "none", which falls back to a photo-only library entry the user
completes later).

Accuracy against a hand-labelled gold set (S1 invoice extraction, A1 material ID):

```bash
uv run python -m scripts.ai_eval.run_eval --invoices eval/invoices --materials eval/materials
```

See `eval/README.md` for the gold format — this repo ships no real invoices/photos, only
the harness and an example gold file.

---

## What's inside the API

The API exposes a small, predictable set of endpoints under `/api/v1/`:

| Area | Endpoints |
|---|---|
| **Authentication** | Sign in, sign out, refresh, "who am I" |
| **Projects** | List, create, view, update, delete |
| **Project members** | Invite by email, list members, accept invitation |
| **Workers** | Add, edit, deactivate workers in a project |
| **Labor entries** | Log attendance and supplement hours |
| **Labor exports** | Excel or PDF for the project or a single worker |
| **Invoices** | Create, list, view, attach files |
| **Notes** | Create, list, mark done, dismiss reminders |
| **Inventory** | Company equipment: warehouses (with address) and rows of tools with quantity, working / damaged, and where they are (warehouse or site) |
| **Notifications** | List, mark read |
| **Admin** | Bulk-add users to projects, manage roles |

Sign-in is JWT-based. Browsers use HTTP-only cookies (with CSRF protection); other clients can use a `Bearer` token in the `Authorization` header.

---

## Security at a glance

- Sign-in is rate-limited (5 attempts per minute) and uses signed, expiring tokens.
- Browser sessions are CSRF-protected.
- Permissions are checked on every request — being a member of one project does not grant access to another.
- Bulk admin operations (e.g. adding a user to many projects at once) are rate-limited per user and per IP.
- Exported PDFs sanitize user-entered text to prevent markup injection.

---

## Support

- Bug reports and feature requests: open an issue on the project tracker.
- Operational issues (deployment, database, email): contact your administrator.

