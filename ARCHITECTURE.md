# Gym Tracker - Architecture

## Overview

Full-stack web application for tracking gym sessions and purchases. Users authenticate via Google OAuth, log training sessions against purchased packages, and view analytics/reports.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.11) |
| Server | Uvicorn (ASGI) |
| ORM | SQLAlchemy 1.4+ |
| Database | MySQL (PyMySQL driver), SQLite for tests |
| Migrations | Alembic |
| Auth | Google OAuth 2.0 / OIDC via Authlib |
| Sessions | Starlette SessionMiddleware (signed cookies) |
| Validation | Pydantic |
| Templates | Jinja2 |
| Frontend | Bootstrap 5.3, vanilla JS, Chart.js |
| CI/CD | GitHub Actions |
| Container | Docker (python:3.11-slim) |
| Registry | GitHub Container Registry (ghcr.io) |

## Project Structure

```
gym_tracker/
├── main.py                         # FastAPI app entry point, all routes
├── gym_tracker/                    # Core Python package
│   ├── __init__.py
│   ├── auth.py                     # OAuth routes + LoginRequiredMiddleware
│   ├── config.py                   # Settings (env vars, DB config, @lru_cache)
│   ├── crud.py                     # Database CRUD operations
│   ├── database.py                 # SQLAlchemy engine + session factory
│   ├── models.py                   # ORM models (5 tables)
│   ├── schemas.py                  # Pydantic request/response schemas
│   └── tests/
│       └── test_crud.py            # Unit tests (in-memory SQLite)
├── templates/                      # Jinja2 HTML templates
│   ├── _nav.html                   # Shared navigation component
│   ├── index.html                  # Dashboard (session logging, purchases)
│   ├── history.html                # Session/purchase history with editing
│   ├── reports.html                # Analytics with Chart.js pie charts
│   ├── privacy.html                # Privacy policy
│   ├── terms.html                  # Terms of service
│   └── admin/                      # Admin-only pages
│       ├── index.html              # Admin dashboard
│       ├── trainers.html           # Trainer CRUD
│       └── packages.html           # Package CRUD
├── alembic/                        # Database migrations
│   └── versions/                   # Migration scripts
├── .github/workflows/ci.yml       # CI: test + build/push Docker image
├── Dockerfile
├── requirements.txt
├── setup.py
└── alembic.ini
```

## Database Schema

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│    users      │     │  purchases   │     │   sessions   │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ id (PK)      │◄────│ logged_by_   │  ┌──│ purchase_id  │
│ google_sub   │     │   user_id    │  │  │ created_by_  │
│ email        │     │ duration_min │  │  │   user_id ──►│users
│ full_name    │     │ total_sess   │◄─┘  │ session_date │
│ role         │     │ sessions_rem │     │ duration_min │
│ is_active    │     │ purchase_date│     │ trainer (str)│ legacy
│ avatar_url   │     │ cost         │     │ trainer_id──►│trainers
│ created_at   │     └──────────────┘     └──────────────┘
│ last_login_at│
└──────────────┘     ┌──────────────┐     ┌──────────────┐
                     │   trainers   │     │   packages   │
                     ├──────────────┤     ├──────────────┤
                     │ id (PK)      │     │ id (PK)      │
                     │ name         │     │ name         │
                     │ is_active    │     │ duration_min │
                     │ created_at   │     │ num_people   │
                     └──────────────┘     │ total_sess   │
                                          │ price_per_s  │
                                          │ is_active    │
                                          └──────────────┘
```

## Authentication & Authorization

1. **Auth flow**: Google OIDC via Authlib -> upsert user by `google_sub` -> set session cookie
2. **Session**: Signed cookie (`gt_session`) via Starlette SessionMiddleware
3. **Middleware**: `LoginRequiredMiddleware` protects all routes except public paths (`/login`, `/auth/callback`, `/logout`, `/healthz`, `/privacy`, `/terms`, `/me`)
4. **Roles**: `user.role` field — `"client"` (default) or `"admin"`
5. **Admin guard**: `require_admin` FastAPI dependency on admin endpoints
6. **Email allowlist**: Optional `ALLOWED_EMAILS` env var restricts who can sign up
7. **Data scoping**: All CRUD operations filter by `user_id` from session — users only see their own data

## API Routes

### Pages (HTML, server-rendered)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | yes | Dashboard |
| GET | `/history` | yes | Session/purchase history |
| GET | `/reports` | yes | Analytics |
| GET | `/admin` | admin | Admin dashboard |
| GET | `/admin/trainers` | admin | Trainer management |
| GET | `/admin/packages` | admin | Package management |
| GET | `/privacy`, `/terms` | no | Legal pages |

### Auth
| Method | Path | Description |
|--------|------|-------------|
| GET | `/login` | Start Google OAuth flow |
| GET | `/auth/callback` | OAuth callback |
| GET | `/logout` | Clear session |
| GET | `/me` | Current user info (debug) |

### Data API (JSON)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/summary/` | yes | Remaining sessions by duration |
| POST | `/sessions/` | yes | Log a session |
| GET | `/history/sessions/` | yes | List user's sessions |
| POST | `/history/api/edit/session/{id}` | yes | Edit session |
| POST | `/history/api/delete/session/{id}` | yes | Delete session |
| POST | `/purchases/` | yes | Create purchase |
| GET | `/history/purchases/` | yes | List user's purchases |
| POST | `/history/api/edit/purchase/{id}` | yes | Edit purchase |
| POST | `/history/api/delete/purchase/{id}` | yes | Delete purchase |
| GET | `/reports/data` | yes | Report data (query: start/end) |
| GET | `/api/trainers/` | yes | List active trainers |
| POST | `/api/trainers/` | admin | Create trainer |
| PUT | `/api/trainers/{id}` | admin | Update trainer |
| DELETE | `/api/trainers/{id}` | admin | Soft-delete trainer |
| GET | `/api/packages/` | yes | List active packages |
| POST | `/api/packages/` | admin | Create package |
| PUT | `/api/packages/{id}` | admin | Update package |
| DELETE | `/api/packages/{id}` | admin | Soft-delete package |
| GET | `/healthz` | no | Health check |

## Key Patterns

- **Soft deletes**: Trainers and packages use `is_active` flag, never hard-deleted
- **Legacy compatibility**: Sessions have both `trainer` (string) and `trainer_id` (FK) fields
- **User ownership**: `logged_by_user_id` on purchases, `created_by_user_id` on sessions — nullable for pre-migration data
- **Config**: `config.py` uses `@lru_cache` for a singleton Settings object from env vars
- **Frontend**: Vanilla JS with Fetch API, Bootstrap modals for forms, Chart.js for reports
- **Testing**: Pytest with in-memory SQLite, tests in `gym_tracker/tests/`

## CI/CD Pipeline

1. **Test** (all pushes + PRs): MySQL 8 service container, Python 3.11, `pytest`
2. **Build & Push** (main only): Docker build -> push to `ghcr.io/{owner}/gym-tracker-app:{latest,sha}`

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `GOOGLE_CLIENT_ID` | OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret |
| `OAUTH_REDIRECT_URI` | OAuth callback URL |
| `SESSION_SECRET` | Cookie signing key |
| `ALLOWED_EMAILS` | Comma-separated email allowlist (optional) |
| `BASE_URL` | Application base URL |
| `DATABASE_URL` | Database connection string |
