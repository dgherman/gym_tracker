# Gym Tracker - Architecture

## Overview

Full-stack web application for tracking gym sessions and purchases. Users authenticate via Google OAuth, log training sessions against purchased packages, and view analytics/reports. Supports 2-person shared packages where a buyer and partner both see the package and can log sessions against it.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (Python 3.11) |
| Server | Uvicorn (ASGI) |
| ORM | SQLAlchemy 1.4+ |
| Database | MySQL (PyMySQL driver), SQLite for tests |
| Migrations | Alembic (auto-runs on container start) |
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
│   ├── auth.py                     # OAuth routes + partner auto-linking on signup
│   ├── config.py                   # Settings (env vars, DB config, @lru_cache)
│   ├── crud.py                     # Database CRUD operations + partner query helpers
│   ├── database.py                 # SQLAlchemy engine + session factory
│   ├── models.py                   # ORM models (5 tables)
│   ├── schemas.py                  # Pydantic request/response schemas
│   └── tests/
│       └── test_crud.py            # Unit tests (in-memory SQLite)
├── templates/                      # Jinja2 HTML templates
│   ├── _nav.html                   # Shared navigation component
│   ├── index.html                  # Dashboard (session logging, purchases, partner email)
│   ├── history.html                # Session/purchase history with partner badges
│   ├── reports.html                # Analytics with Chart.js pie charts (incl. partner chart)
│   ├── privacy.html                # Privacy policy
│   ├── terms.html                  # Terms of service
│   └── admin/                      # Admin-only pages
│       ├── index.html              # Admin dashboard
│       ├── trainers.html           # Trainer CRUD
│       └── packages.html           # Package CRUD
├── alembic/                        # Database migrations
│   └── versions/                   # Migration scripts
├── docs/plans/                     # Design documents
├── .github/workflows/ci.yml       # CI: test + build/push Docker image
├── Dockerfile
├── requirements.txt
├── setup.py
└── alembic.ini
```

## Database Schema

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│    users      │     │    purchases      │     │    sessions       │
├──────────────┤     ├──────────────────┤     ├──────────────────┤
│ id (PK)      │◄────│ logged_by_       │  ┌──│ purchase_id       │
│ google_sub   │     │   user_id        │  │  │ created_by_       │
│ email        │◄────│ partner_user_id  │  │  │   user_id ───────►│users
│ full_name    │     │ partner_email    │  │  │ partner_user_id──►│users
│ role         │     │ num_people       │  │  │ session_date      │
│ is_active    │     │ duration_min     │◄─┘  │ duration_min      │
│ avatar_url   │     │ total_sess       │     │ trainer (str)     │ legacy
│ created_at   │     │ sessions_rem     │     │ trainer_id───────►│trainers
│ last_login_at│     │ purchase_date    │     └──────────────────┘
└──────────────┘     │ cost             │
                     └──────────────────┘
                     ┌──────────────┐     ┌──────────────┐
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

## Two-Person Session Sharing

Packages with `num_people >= 2` support partner sharing:

- **Purchase**: Buyer specifies partner by email. `partner_email` is always stored; `partner_user_id` is set if the partner has an account (or auto-linked when they sign up via OAuth).
- **Visibility**: Both buyer and partner see the purchase in their dashboard. Partner sees cost as $0.
- **Session logging**: Both users can log sessions that consume from the shared package. Optional per-session partner email override.
- **Session visibility**: A user sees sessions they created, sessions where they're the per-session partner, sessions from purchases they own, and sessions from purchases where they're the partner.
- **Display**: Partner name always shows the *other* person (buyer sees partner name, partner sees buyer name).
- **Auto-linking**: On OAuth signup, purchases with matching `partner_email` and NULL `partner_user_id` are automatically linked to the new user.
- **Summary**: Groups by `(duration_minutes, num_people)` to distinguish 1-person and 2-person packages with the same duration.

Key helpers in `crud.py`:
- `_user_purchase_filter(user_id)` — OR filter for owner/partner on purchases
- `_user_session_ids(db, user_id)` — subquery returning distinct session IDs visible to a user (prevents duplicate rows in aggregates)
- `_annotate_purchases(db, purchases, user_id)` — adds `is_owner`, `partner_name`, zeroes cost for partners
- `_annotate_session(sess, purchase, user_id)` — adds `is_owner`, `partner_name`, `num_people` to sessions

## Authentication & Authorization

1. **Auth flow**: Google OIDC via Authlib -> upsert user by `google_sub` -> auto-link partner purchases -> set session cookie
2. **Session**: Signed cookie (`gt_session`) via Starlette SessionMiddleware
3. **Middleware**: `LoginRequiredMiddleware` protects all routes except public paths (`/login`, `/auth/callback`, `/logout`, `/healthz`, `/privacy`, `/terms`, `/me`)
4. **Roles**: `user.role` field — `"client"` (default) or `"admin"`
5. **Admin guard**: `require_admin` FastAPI dependency on admin endpoints
6. **Email allowlist**: Optional `ALLOWED_EMAILS` env var restricts who can sign up
7. **Data scoping**: All CRUD operations filter by `user_id` from session — users see their own data plus shared partner data

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
| GET | `/auth/callback` | OAuth callback + partner auto-link |
| GET | `/logout` | Clear session |
| GET | `/me` | Current user info (debug) |

### Data API (JSON)
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/summary/` | yes | Remaining sessions by (duration, num_people) |
| POST | `/sessions/` | yes | Log a session (accepts partner_email, num_people) |
| GET | `/history/sessions/` | yes | List user's sessions (incl. partner sessions) |
| POST | `/history/api/edit/session/{id}` | yes | Edit session (owner only) |
| POST | `/history/api/delete/session/{id}` | yes | Delete session (owner only) |
| POST | `/purchases/` | yes | Create purchase (accepts partner_email, num_people) |
| GET | `/history/purchases/` | yes | List user's purchases (incl. shared, partner at $0) |
| POST | `/history/api/edit/purchase/{id}` | yes | Edit purchase (owner only) |
| POST | `/history/api/delete/purchase/{id}` | yes | Delete purchase (owner only) |
| GET | `/reports/data` | yes | Report data incl. minutes_by_partner |
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
- **Partner sharing**: Purchases and sessions have `partner_user_id` (FK) + `partner_email` (on purchases) for 2-person package support
- **Deduplication**: Session visibility uses a subquery (`_user_session_ids`) to return distinct IDs, preventing inflated aggregates from JOIN + OR conditions
- **ORM safety**: `_annotate_purchases` calls `db.expunge()` before mutating `cost` on partner views to prevent flushing $0 to the database
- **Config**: `config.py` uses `@lru_cache` for a singleton Settings object from env vars
- **Frontend**: Vanilla JS with Fetch API, Bootstrap modals for forms, Chart.js for reports (3 pie charts: by trainer, by duration, by partner)
- **Testing**: Pytest with in-memory SQLite, tests in `gym_tracker/tests/`

## CI/CD Pipeline

1. **Test** (all pushes + PRs): MySQL 8 service container, Python 3.11, `pytest`
2. **Build & Push** (main only): Docker build -> push to `ghcr.io/{owner}/gym-tracker-app:{latest,sha}`
3. **Migrations**: `alembic upgrade head` runs automatically on container start (before uvicorn)

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
