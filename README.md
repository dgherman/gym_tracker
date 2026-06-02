# gym_tracker
Simple gym sessions tracker

## Features

- **Session Logging**: Log gym sessions against purchased packages with duration, trainer, and optional partner sharing.
- **Package Management**: Buy personal or shared 2-person packages; automatic partner linking via email.
- **Activity Tracking**: Optionally log structured activities per session, grouped by admin-managed categories (Strength, Cardio, Mobility, Other) with category-defined metric fields (e.g. reps, weight in lbs, duration). A global activity library lets any user create activities that everyone can use; log at session-create time or edit retroactively.
- **Reports & History**: Session history, analytics by trainer/duration/partner, and remaining-session summaries.
- **Admin Tools**: Manage trainers, packages, and the activity library (categories, metric fields, deactivation).

## Changelog

### 2026-06-02 – Activity Tracking

- Added optional structured activity logging to sessions with category-based metric schemas (Strength, Cardio, Mobility, Other).
- New tables: `activity_categories`, `category_fields` (admin-defined metric schema), `activities` (global, deduplicated library), `session_activities` (one JSON `values` row per activity per session).
- Global activity library: any user creates activities; deduplicated case-insensitively per category; soft-deleted by admin.
- Log at session-create time and add/edit/remove retroactively on existing sessions; values validated server-side against the category's active fields; reconciliation upserts by id and deletes omitted rows. Activities cascade-delete with their session.
- Admin page `/admin/activities` for managing categories, fields, and the activity library.
- Dev-only `GET /dev/login` (gated by `DEV_LOGIN` env) for local UI testing without Google OAuth.
