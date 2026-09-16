# TaskFlow — Production-Ready Task Management Application
### Kovai.co Engineering Assignment Submission & Technical Documentation

---

## 1. Project Overview & Summary

**TaskFlow** is a modern, enterprise-grade Task Management SaaS platform developed using **Flask (Python)**, **SQLite**, **Bootstrap 5**, and **Google OAuth 2.0**. 

The application is engineered from the ground up to solve the core productivity challenge: helping individual professionals and distributed team members organize, prioritize, track, and complete daily tasks in an intuitive, high-density, zero-clutter workspace.

### Key Highlights
- **Google OAuth 2.0 Authentication**: Seamless, secure sign-in via Google accounts with automated session lifecycle management.
- **Strict Multi-User Data Isolation**: Complete tenant isolation at both the application and database query levels; users can only view, edit, or delete their own tasks (enforcing strict **HTTP 403 Forbidden** security boundaries).
- **Comprehensive Task CRUD**: Full creation, viewing, modal editing, inline status updates, and deletion with safety confirmations.
- **Advanced Productivity Engine**: 3-level priority tagging (High, Medium, Low), ISO due-date scheduling with dynamic urgency indicators (*Overdue*, *Due Today*, *Upcoming*, *Done*), and multi-mode sorting.
- **Instant Real-Time Search & Filtering**: Zero-latency client-side search synchronized with server-side query filters.
- **High-Density SaaS Dashboard**: Inspired by **Linear**, **Notion**, and **Jira Cloud**, all critical workspace elements (Navbar, KPIs, Filter Sidebar, and Task Workspace) fit **above the fold on a 1080p display**.
- **Production-Hardened**: Incorporates Werkzeug `ProxyFix` for cloud load balancers, database triggers for data integrity, parameterized queries for SQL injection prevention, and a 24-test automated QA test suite.

---

## 2. Technical Stack & Rationale

| Layer | Technology | Version | Engineering Rationale |
|---|---|---|---|
| **Backend Framework** | **Flask (Python)** | 3.0.3 | Lightweight, fast, predictable WSGI micro-framework allowing granular control over routing, session handling, and application context. |
| **WSGI Server** | **Gunicorn** | 22.0.0 | High-performance, battle-tested UNIX WSGI HTTP server for production deployment on Render. |
| **Authentication** | **Flask-Dance** + **OAuthlib** | >=7.0.0 | Official Google OAuth 2.0 implementation with token persistence, signal handling, and secure profile fetching. |
| **Database** | **SQLite 3** | Embedded (Python 3.14) | Zero-configuration, ACID-compliant relational storage with Write-Ahead Logging (WAL) and foreign key enforcement. |
| **Environment Config** | **python-dotenv** | >=1.0.0 | 12-Factor App methodology; keeps sensitive credentials (`SECRET_KEY`, OAuth tokens) out of source control. |
| **Frontend Styling** | **Bootstrap 5** + **Custom CSS** | 5.3.3 | Enterprise-grade component system enhanced with custom CSS design tokens (`#6C63FF` primary palette, glassmorphism, micro-shadows). |
| **Typography & Icons** | **Google Fonts (Inter)** + **Bootstrap Icons** | 1.11.3 | Modern, crisp UI typography with high scannability and standard SVG iconography. |
| **Hosting Platform** | **Render.com** | Free Web Service | Cloud PaaS with automated continuous deployment from GitHub `main` branch. |

---

## 3. Architecture & Engineering Approach

TaskFlow follows a **modular, layered architecture** adhering to separation of concerns:

```
┌────────────────────────────────────────────────────────┐
│                   CLIENT BROWSER                       │
│  (Desktop, Laptop, Tablet, Mobile Touchscreens)        │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS
                            ▼
┌────────────────────────────────────────────────────────┐
│             RENDER CLOUD LOAD BALANCER                 │
│  (SSL/TLS Termination, Reverse Proxy, Static Caching)   │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP + X-Forwarded-Proto
                            ▼
┌────────────────────────────────────────────────────────┐
│               GUNICORN WSGI SERVER                     │
│               app.wsgi_app = ProxyFix(...)             │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────┴────────────────────────────┐
│                    FLASK APPLICATION                   │
│                                                        │
│  ┌─────────────────┐ ┌──────────────────────────────┐  │
│  │ Authentication  │ │ Routing & Business Logic     │  │
│  │  - Flask-Dance  │ │  - Public: /, /login, /health│  │
│  │  - is_auth()    │ │  - Protected: /dashboard     │  │
│  │  - @login_req   │ │  - CRUD: /add, /edit, /delete│  │
│  └─────────────────┘ └──────────────────────────────┘  │
│                                                        │
│  ┌─────────────────┐ ┌──────────────────────────────┐  │
│  │ Presentation    │ │ Data & Validation Layer      │  │
│  │  - Jinja2       │ │  - validate_task_input()     │  │
│  │  - base.html    │ │  - verify_task_ownership()   │  │
│  │  - dashboard    │ │  - enrich_task()             │  │
│  └─────────────────┘ └──────────────────────────────┘  │
└───────────────────────────┬────────────────────────────┘
                            │ SQLite C-API
                            ▼
┌────────────────────────────────────────────────────────┐
│                 SQLITE DATABASE (tasks.db)             │
│   - WAL Journal Mode (High Concurrent Read Throughput) │
│   - B-Tree Indexes on (user_id, status, priority, due) │
│   - CHECK Constraints & Triggers for Data Integrity    │
└────────────────────────────────────────────────────────┘
```

### Key Engineering Paradigms Applied:
1. **Per-Request Connection Management (`g.db`)**: Connections are initialized on first access per request on Flask's `g` proxy and cleanly closed during application teardown (`@app.teardown_appcontext`), avoiding connection leaks.
2. **Dual-Gate Authentication Check**: To avoid infinite redirect loops or unauthorized access from orphaned OAuth cookies, `is_authenticated()` verifies both `google.authorized` and the cached session user. If inconsistent, the stale token is automatically purged.
3. **Defense-in-Depth Ownership Verification**:
   - Data read queries explicitly filter by `WHERE user_id = ?`.
   - Data write operations (`update`, `edit`, `delete`) invoke `verify_task_ownership(task_id, user_id)`. Any attempt by an authenticated user to alter another user's task results in an immediate **HTTP 403 Forbidden** security response.
4. **Resilient Dual-Trigger Modals**: Modals bind both declarative `data-bs-toggle="modal"` attributes and programmatic JavaScript listeners with DOM fallback routines, ensuring reliability across mobile touchscreens and desktop browsers.

---

## 4. Database Schema & Data Integrity

The database is built on SQLite with Write-Ahead Logging (WAL) and idempotent schema migrations:

### `tasks` Table Structure

| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `INTEGER` | `PRIMARY KEY AUTOINCREMENT` | Unique task identifier |
| `user_id` | `TEXT` | `NOT NULL` | Google Subject ID of the task owner |
| `user_email` | `TEXT` | `NOT NULL` | Owner's Google account email address |
| `title` | `TEXT` | `NOT NULL` | Task title (1 to 200 characters) |
| `description` | `TEXT` | `NULLABLE` | Detailed specifications (max 2,000 characters) |
| `status` | `TEXT` | `NOT NULL DEFAULT 'Planned'` | `CHECK(status IN ('Planned','In Progress','Complete'))` |
| `priority` | `TEXT` | `NOT NULL DEFAULT 'Medium'` | `CHECK(priority IN ('Low','Medium','High'))` |
| `due_date` | `TEXT` | `NULLABLE` | ISO date string (`YYYY-MM-DD`) |
| `created_at` | `TEXT` | `NOT NULL` | ISO-8601 UTC timestamp of creation |

### Indexes & Performance
```sql
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_user_priority ON tasks(user_id, priority);
CREATE INDEX IF NOT EXISTS idx_tasks_user_due_date ON tasks(user_id, due_date);
```

### Integrity Triggers
To enforce constraints across pre-existing tables migrated in earlier phases, SQLite triggers abort any invalid insert or update:
```sql
CREATE TRIGGER IF NOT EXISTS trg_validate_priority_insert
BEFORE INSERT ON tasks FOR EACH ROW
WHEN NEW.priority NOT IN ('Low', 'Medium', 'High')
BEGIN
    SELECT RAISE(ABORT, 'Invalid priority value. Must be Low, Medium, or High.');
END;
```

---

## 5. Security & Privacy Audit

1. **Strict Multi-User Isolation**: No user can read, query, edit, update, or delete tasks belonging to another user.
2. **Zero Plaintext Secrets**: All sensitive API keys, OAuth client secrets, and session salts are read from environment variables via `os.environ`. The `.env` file is strictly ignored in `.gitignore`.
3. **Session Hardening**: Flask session stores only verified user profile identifiers. Stale tokens are purged via `_clear_oauth_token()`.
4. **CSRF & HTTP Method Discipline**: Destructive actions (Delete, Edit, Update, Logout) strictly require `POST` requests.
5. **SQL Injection Prevention**: 100% of database interactions use parameterized SQL queries (`?` placeholders) with bound parameter tuples; no string interpolation is ever used in queries.
6. **XSS Protection**: Jinja2 auto-escaping is active across all templates. Data attributes (`data-task-title`) are used for JavaScript dialogs to prevent script injection via quotes or special characters.

---

## 6. Testing & Quality Assurance

TaskFlow includes an automated test suite with **24 unit and integration tests (100% passing)**:

```bash
python -m unittest discover -s scratch -p "test_*.py"
```

### Verified Test Capabilities:
- **Authentication**: Unauthorized redirect enforcement (302 -> `/login`), login page rendering, session persistence, and logout teardown.
- **CRUD Operations**: Task creation with boundary inputs (1–200 characters), validation rejection for empty or oversized inputs, inline status progression, modal editing, and deletion.
- **Security & Multi-Tenant Boundaries**: User A and User B cross-access tests; confirms HTTP 403 Forbidden on illegal edit/delete attempts.
- **Search & Filters**: Status, priority, due date filters, and multi-field keyword search queries.
- **Database & Migration**: Schema migration idempotency and trigger constraint enforcement.
- **Error Handlers**: Graceful 404, 405, and 500 JSON APIs and user-facing redirects.

---

## 7. Render.com Deployment Guide

Deploying TaskFlow to Render takes under 3 minutes:

### Step 1: Push Code to GitHub
Ensure the latest code is on GitHub:
```bash
git push origin main
```

### Step 2: Create Web Service on Render
1. Log in to [Render Dashboard](https://dashboard.render.com).
2. Click **New +** → **Web Service**.
3. Connect your GitHub repository: `manojsekar164-crypto/Kovai.co-Assignment`.
4. Configure service settings:
   - **Name**: `taskflow-app` (or any preferred name)
   - **Region**: Oregon (US West) or Singapore
   - **Branch**: `main`
   - **Root Directory**: Leave blank
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: `Free`

### Step 3: Configure Environment Variables on Render
In the **Environment Variables** tab of your Render service, add:

| Key | Value | Description |
|---|---|---|
| `SECRET_KEY` | *(A random 64-char string)* | Generates secure Flask session cookies |
| `GOOGLE_OAUTH_CLIENT_ID` | `744134993738-...apps.googleusercontent.com` | From Google Cloud Console |
| `GOOGLE_OAUTH_CLIENT_SECRET` | `GOCSPX-...` | From Google Cloud Console |
| `DATABASE_URL` | `tasks.db` | Local SQLite path |

*(Note: On Render, `OAUTHLIB_INSECURE_TRANSPORT` must NOT be set, as production runs over secure HTTPS).*

### Step 4: Update Google Cloud Console Authorized Redirect URI
1. Go to [Google Cloud Console Credentials](https://console.cloud.google.com/apis/credentials).
2. Select your OAuth 2.0 Client ID.
3. Under **Authorized redirect URIs**, add your live Render URL callback:
   ```
   https://<your-service-name>.onrender.com/login/google/authorized
   ```
4. Under **Authorized JavaScript origins**, add:
   ```
   https://<your-service-name>.onrender.com
   ```
5. Click **Save**.

Your live application is now fully hosted and accessible worldwide!

---

## 8. Answers for Assignment Submission Form

Use these concise, professional answers when filling out your evaluation form:

### Q1: What was your engineering approach to this project?
> "I designed TaskFlow following clean, modular software engineering practices: a lightweight Flask backend paired with SQLite for predictable relational persistence, and a modern Bootstrap 5 SaaS interface styled after Linear and Notion. Development was phased systematically:
> 1. Core CRUD and connection pooling with SQLite WAL mode.
> 2. Secure Google OAuth 2.0 authentication with dual-gate session validation.
> 3. Strict multi-user data isolation and tenant ownership enforcement (HTTP 403).
> 4. Advanced productivity features (priorities, due-date urgency badges, real-time client search, and multi-mode sorting).
> 5. High-density responsive UI/UX refactoring ensuring key metrics and table rows fit above the fold.
> 6. Comprehensive 24-test automated QA suite and reverse-proxy hardening (ProxyFix) for cloud deployment."

### Q2: What tech stack did you use and why?
> "I selected **Python/Flask** for the backend due to its speed, simplicity, and granular routing control; **Gunicorn** as the production WSGI server; **Flask-Dance** for Google OAuth 2.0; **SQLite** with WAL mode and B-tree indexes for zero-maintenance ACID data storage; and **Bootstrap 5 + Vanilla CSS** for a responsive, high-density, accessible frontend. This stack eliminates heavy dependencies while delivering an enterprise-grade SaaS experience."

### Q3: How did you implement and test data isolation across multiple users?
> "Multi-tenant data isolation is enforced at both the database query layer and application route controllers. All dashboard select statements filter strictly on `WHERE user_id = ?`. Every state-changing route (`/update-task`, `/edit-task`, `/delete-task`) invokes a dedicated ownership verification helper (`verify_task_ownership`). If an authenticated user attempts to access or modify a task belonging to another Google ID, the application blocks the action, logs a security warning, and returns an HTTP 403 Forbidden response. This was verified through automated test suites where mock clients representing Alice, Bob, and Charlie attempted cross-user edits and deletions."

### Q4: What challenges did you face and how did you resolve them?
> "1. **Reverse Proxy SSL Termination**: In cloud hosting environments like Render, load balancers terminate HTTPS and forward requests as HTTP, which causes OAuth redirect URIs to generate with `http://` and trigger Google OAuth rejection. I resolved this by applying Werkzeug's `ProxyFix` middleware to parse `X-Forwarded-Proto`.
> 2. **Subresource Integrity (SRI) Hash Mismatch**: During frontend stabilization, an incorrect integrity hash on the Bootstrap CDN bundle caused modern browsers to block script execution, disabling modal triggers. I resolved this by correcting the CDN script tag and adding resilient JavaScript event triggers with DOM fallback handlers.
> 3. **SQL Injection and Quote Escaping in UI**: Task titles containing apostrophes caused JavaScript syntax errors in inline delete confirmation handlers. I decoupled event handling from inline scripts using HTML5 `data-task-title` attributes, ensuring safe handling of all unicode strings."
