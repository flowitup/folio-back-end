# Construction Backend

Flask API for the Construction Management System, built with hexagonal architecture.

## Tech Stack

- **Framework**: Flask 3.0+
- **Language**: Python 3.12+
- **Package Manager**: uv
- **Task Queue**: RQ (Redis Queue)
- **Architecture**: Hexagonal (Ports & Adapters)

## Project Structure

```
construction-back-end/
├── app/
│   ├── __init__.py         # Application factory (create_app)
│   └── api/
│       └── v1/
│           └── __init__.py # API v1 routes
├── config/
│   └── __init__.py         # Configuration from environment
├── infrastructure/
│   └── queue/
│       └── rq_worker.py    # RQ worker entrypoint
├── outbox/
│   └── processor.py        # Outbox pattern processor
├── wiring.py               # DI container (ports → adapters)
├── tasks.py                # Background tasks (email, etc.)
├── pyproject.toml          # Python project configuration
├── Dockerfile              # Container image definition
└── README.md               # This file
```

## Hexagonal Architecture

This project follows hexagonal (ports & adapters) architecture:

- **Core Domain**: Pure business logic with no external dependencies
- **Ports**: Interfaces (protocols) defining how core interacts with outside world
- **Adapters**: Concrete implementations of ports (database, email, etc.)

### Core Purity Rule

> The core domain should **never** import from infrastructure or adapters.
> All dependencies flow inward through ports defined in `wiring.py`.

## Getting Started

### Prerequisites

- Python 3.12+ (installed via uv)
- uv (Python package manager)
- Redis (for task queue)
- PostgreSQL (or SQLite for development)

### Installing uv

If you don't have uv installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

### Installation

1. Install Python 3.12 (if not already installed):

```bash
uv python install 3.12
```

2. Create a virtual environment and install dependencies:

```bash
uv sync

# For development with dev tools:
uv sync --all-extras
```

3. Set up environment variables:

```bash
cp .env.example .env
# Edit .env with your configuration
```

### Running the Application

Start the Flask development server:

```bash
uv run flask run
# or with debug mode:
uv run flask run --debug
```


The API will be available at [http://localhost:5000](http://localhost:5000).

### Health Check

Verify the server is running:

```bash
curl http://localhost:5000/health
# Response: {"status": "ok"}
```

## Running the RQ Worker

The application uses RQ (Redis Queue) for background task processing.

1. Ensure Redis is running:

```bash
# Using Docker:
docker run -d -p 6379:6379 redis:7

# Or install locally
redis-server
```

2. Start the worker:

```bash
uv run python -m infrastructure.queue.rq_worker

# Or specify queues:
uv run python -m infrastructure.queue.rq_worker default emails outbox
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `DATABASE_URL` | No | `sqlite:///dev.db` | Database connection string |
| `SECRET_KEY` | Yes (prod) | `dev-secret-key` | Secret key for sessions |
| `JWT_SECRET_KEY` | Yes (prod) | `dev-jwt-secret` | Secret key for JWT tokens |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL |
| `EMAIL_PROVIDER` | No | `smtp` | Email provider type |
| `SMTP_HOST` | No | `localhost` | SMTP server host |
| `SMTP_PORT` | No | `587` | SMTP server port |
| `SMTP_USER` | No | - | SMTP username |
| `SMTP_PASS` | No | - | SMTP password |
| `SMTP_USE_TLS` | No | `true` | Use TLS for SMTP |
| `FLASK_DEBUG` | No | `false` | Enable debug mode |

## API Endpoints

### Health Check
- `GET /health` - Returns `{"status": "ok"}`

### Authentication (API v1)
- `POST /api/v1/auth/login` - Authenticate and get JWT tokens (rate limited: 5/min)
- `POST /api/v1/auth/logout` - Logout and revoke token
- `POST /api/v1/auth/refresh` - Refresh access token
- `GET /api/v1/auth/me` - Get current user info

#### CSRF Protection

When using cookies for authentication (browser clients), CSRF protection is enabled.

**Frontend Integration:**

1. On login, the server sets cookies including a CSRF token
2. For all state-changing requests (POST, PUT, DELETE), include the CSRF token header:

```javascript
// After login, get CSRF token from cookie
const csrfToken = document.cookie
  .split('; ')
  .find(row => row.startsWith('csrf_access_token='))
  ?.split('=')[1];

// Include in subsequent requests
fetch('/api/v1/auth/logout', {
  method: 'POST',
  headers: {
    'X-CSRF-TOKEN': csrfToken,
    'Content-Type': 'application/json'
  },
  credentials: 'include'
});
```

**API Clients (non-browser):**

Use the `Authorization: Bearer <token>` header instead of cookies. CSRF protection only applies to cookie-based auth.

### Projects (API v1)
- `GET /api/v1/projects` - List projects (requires `project:read`)
- `POST /api/v1/projects` - Create project (requires `project:create`)
- `GET /api/v1/projects/:id` - Get project (requires `project:read`)
- `PUT /api/v1/projects/:id` - Update project (requires `project:update`)
- `DELETE /api/v1/projects/:id` - Delete project (requires `project:delete`)

### Users (API v1 - Stubs)
- `GET /api/v1/users` - List users
- `GET /api/v1/users/:id` - Get user

## Development

### Running Tests

```bash
uv run pytest
```

### Code Formatting

```bash
uv run black .
uv run ruff check --fix .
```

### Type Checking

```bash
uv run mypy .
```

## Docker

### Using Docker Compose (Recommended)

Start all services (API, worker, PostgreSQL, Redis):

```bash
# Start all services in the background
docker compose up -d

# View logs
docker compose logs -f

# Stop all services
docker compose down
```

#### Environment Configuration

Create a `.env` file to customize the configuration:

```bash
# Database
POSTGRES_USER=myuser
POSTGRES_PASSWORD=mysecurepassword
POSTGRES_DB=construction

# Application
SECRET_KEY=your-production-secret-key
FLASK_DEBUG=false

# Email
EMAIL_PROVIDER=smtp
SMTP_HOST=smtp.example.com
SMTP_USER=user@example.com
SMTP_PASS=password
```

#### Services

| Service | Port | Description |
|---------|------|-------------|
| `api` | 5000 | Flask API server |
| `worker` | - | RQ background worker |
| `db` | 5432 | PostgreSQL database |
| `redis` | 6379 | Redis for task queue |

### Using Docker Only

Build and run the API container standalone:

```bash
# Build the image
docker build -t construction-backend .

# Run the container
docker run -p 5000:5000 --env-file .env construction-backend

# Run the worker
docker run --env-file .env construction-backend python -m infrastructure.queue.rq_worker
```
