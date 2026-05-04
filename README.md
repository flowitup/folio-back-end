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
- **Invoices** — Issue Client, Labor and Supplier invoices. Each one has line items, totals, and a clean print-ready view.
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
   uv run python -m infrastructure.queue.rq_worker
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
| `EMAIL_PROVIDER`, `SMTP_*` | Outgoing email — invitations, password resets, reminders. |

A full template lives in `.env.example`.

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

