# TaskFlow — Modern Task Management Platform
### Enterprise-Ready SaaS Dashboard | Kovai.co Engineering Assignment

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Flask 3.0.3](https://img.shields.io/badge/Flask-3.0.3-black.svg)](https://flask.palletsprojects.com/)
[![Bootstrap 5.3.3](https://img.shields.io/badge/Bootstrap-5.3.3-purple.svg)](https://getbootstrap.com/)
[![SQLite3](https://img.shields.io/badge/SQLite-WAL%20Mode-blue.svg)](https://www.sqlite.org/)
[![OAuth 2.0](https://img.shields.io/badge/Google-OAuth%202.0-red.svg)](https://developers.google.com/identity)
[![Tests](https://img.shields.io/badge/Tests-24%20Passing%20(100%25)-success.svg)](./scratch)

---

## 📌 Project Overview

**TaskFlow** is an enterprise-grade task management web application built with **Flask**, **SQLite**, **Bootstrap 5**, and **Google OAuth 2.0**.

Designed after modern productivity platforms like **Linear**, **Notion**, and **Jira Cloud**, TaskFlow delivers a high-density, above-the-fold SaaS dashboard engineered for clarity, speed, and strict multi-user tenant isolation.

---

## 🚀 Key Features

- 🔐 **Google OAuth 2.0 Authentication**: One-click Google sign-in with automatic user profile retrieval (avatar, name, email) and secure session lifecycle management.
- 🛡️ **Multi-User Data Isolation**: Complete tenant privacy. Users can only view, edit, or delete their own tasks. Cross-user modification attempts strictly return **HTTP 403 Forbidden**.
- 📋 **Full Task CRUD**:
  - **Create**: Modal dialog with title, rich description, priority, initial status, and due date.
  - **Read**: Dense SaaS table view with color-coded status stripes, priority badges, and timestamps.
  - **Update**: Modal-based editing and instant inline status dropdowns directly on table rows.
  - **Delete**: Protected action with custom JavaScript confirmation dialogs preventing accidental deletions.
- 🎯 **3-Tier Priority System**: Color-coded badges for `High` (🔥 Red), `Medium` (🟡 Amber), and `Low` (🔵 Blue).
- 📅 **Smart Due Dates & Urgency**: Dynamic indicators for **Overdue**, **Due Today**, **Upcoming**, and **Done**.
- ⚡ **Real-Time Client & Server Search**: Zero-latency instant typing filter synchronized with server-side query filters.
- 📊 **Productivity Metrics**: 4 single-row KPI counters (Total Tasks, Planned, In Progress, Complete) and completion rate progress indicator.
- 📱 **Fully Responsive Layout**: High-density desktop table layout transitioning into mobile card stacks with custom `data-label` pseudo-elements.

---

## 🏗️ System Architecture

```
┌────────────────────────────────────────────────────────┐
│                   CLIENT BROWSER                       │
│      (Desktop, Laptop, Tablet, Mobile Touch)           │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS
                            ▼
┌────────────────────────────────────────────────────────┐
│             REVERSE PROXY / LOAD BALANCER              │
│       (Render SSL Termination, X-Forwarded-Proto)      │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP + Headers
                            ▼
┌────────────────────────────────────────────────────────┐
│                GUNICORN WSGI SERVER                    │
│            app.wsgi_app = ProxyFix(...)                │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────┴────────────────────────────┐
│                    FLASK APPLICATION                   │
│  - Routes: Public (/, /login) vs Protected (/dashboard)│
│  - Auth: Flask-Dance Google OAuth 2.0                  │
│  - Security: @login_required, verify_task_ownership()  │
│  - UI: Jinja2 Templates, Bootstrap 5.3, Custom CSS     │
└───────────────────────────┬────────────────────────────┘
                            │ Parameterized SQL
                            ▼
┌────────────────────────────────────────────────────────┐
│                 SQLITE DATABASE (tasks.db)             │
│   - Write-Ahead Logging (WAL) & Foreign Keys           │
│   - B-Tree Indexes on user_id, status, priority, due   │
│   - Database Triggers for Status & Priority Integrity  │
└────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack & Dependencies

| Component | Technology | Version | Purpose |
|---|---|---|---|
| **Backend** | Python / Flask | 3.0.3 | Lightweight, robust WSGI framework |
| **WSGI Server** | Gunicorn | 22.0.0 | Production UNIX HTTP server |
| **Authentication** | Flask-Dance | >=7.0.0 | Google OAuth 2.0 integration |
| **Database** | SQLite3 | Embedded | ACID relational storage with WAL mode |
| **Config** | python-dotenv | >=1.0.0 | 12-Factor App environment variable isolation |
| **Frontend** | Bootstrap 5 | 5.3.3 | Responsive UI component framework |
| **Icons & Fonts** | Bootstrap Icons + Inter | 1.11.3 | Enterprise UI iconography and typography |
| **Reverse Proxy** | Werkzeug ProxyFix | Integrated | Forwarded SSL/Proto header parsing for cloud |

---

## 🔒 Security & Data Integrity

1. **Strict Ownership Boundaries**: Every state-changing route (`/update-task`, `/edit-task`, `/delete-task`) invokes `verify_task_ownership(task_id, user_id)` before executing queries. Unauthorized attempts return **HTTP 403 Forbidden**.
2. **SQL Injection Resistance**: 100% of queries use parameterized bindings (`?`); zero string formatting in SQL statements.
3. **Database-Level Integrity Triggers**: SQLite triggers reject any insert/update with invalid status or priority values.
4. **Environment Isolation**: Production secrets and credentials reside in `.env` (strictly ignored by `.gitignore`).
5. **XSS Protection**: Automatic HTML escaping across Jinja2 templates; dialog parameters are transferred safely via HTML5 data attributes (`data-task-title`).

---

## ⚙️ Local Setup & Installation

### 1. Clone Repository
```bash
git clone https://github.com/manojsekar164-crypto/Kovai.co-Assignment.git
cd Kovai.co-Assignment
```

### 2. Create and Activate Virtual Environment
```bash
python -m venv venv

# Windows (PowerShell):
.\venv\Scripts\Activate.ps1

# macOS / Linux:
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your credentials in `.env`:
```env
SECRET_KEY=your-random-secret-key
GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
OAUTHLIB_INSECURE_TRANSPORT=1  # Local development only
```

### 5. Run the Application
```bash
python app.py
```
Open your browser at **`http://127.0.0.1:5000`**.

---

## 🧪 Automated Testing Suite

The application includes an extensive test suite covering authentication, CRUD, multi-tenant isolation, database migrations, and error handlers:

```bash
# Run all automated tests
python -m unittest discover -s scratch -p "test_*.py"
```

### Test Results Summary
```
[PASS]  1. Unauthenticated requests to /dashboard, /add-task, /edit-task, /delete-task are blocked (302 -> /login)
[PASS]  2. Login page renders application branding and official Google OAuth button
[PASS]  3. Authenticated user profile (name, email, avatar) displays in the navbar
[PASS]  4. Logout clears OAuth tokens, flushes the session, and redirects to /login
[PASS]  5. Create Task saves title, description, priority, status, and due_date to DB
[PASS]  6. Input validation rejects empty titles, oversized inputs, and malformed dates
[PASS]  7. Edit Task modifies title, description, status, priority, and due_date accurately
[PASS]  8. Inline status dropdown updates task status directly from the dashboard table
[PASS]  9. Delete Task removes the record and automatically recalculates KPI statistics
[PASS] 10. Multi-User Isolation: User A cannot see User B's tasks on /dashboard
[PASS] 11. Security Ownership: User B receives HTTP 403 Forbidden when attempting to modify User A's task
[PASS] 12. Filtering & Search: Status filter, Priority filter, Due Date filter, and Sort by work seamlessly
[PASS] 13. Database Schema: migrate_db() runs idempotently without duplicate-column errors
[PASS] 14. Data Constraints: SQLite triggers and CHECK constraints enforce status and priority values
[PASS] 15. Error Handling: 404, 405, and 500 return graceful JSON for APIs and branded redirects for web
[PASS] 16. Security Standards: .env is in .gitignore, and no secrets or tokens are exposed

24 out of 24 Tests Passed (100% Pass Rate)
```

---

## 🌐 Deploying to Render.com

1. Create a new **Web Service** on [Render](https://dashboard.render.com).
2. Connect your GitHub repository: `manojsekar164-crypto/Kovai.co-Assignment`.
3. Configure settings:
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app`
   - **Instance Type**: `Free`
4. Add Environment Variables:
   - `SECRET_KEY`: *(random 32+ character string)*
   - `GOOGLE_OAUTH_CLIENT_ID`: *(Google OAuth Client ID)*
   - `GOOGLE_OAUTH_CLIENT_SECRET`: *(Google OAuth Client Secret)*
5. In **Google Cloud Console**, add your live Render callback:
   - **Authorized redirect URI**: `https://<your-service>.onrender.com/login/google/authorized`
   - **Authorized JavaScript origin**: `https://<your-service>.onrender.com`

---

## 📄 License & Attribution

Developed for the **Kovai.co Engineering Assignment**.  
All rights reserved © 2026 TaskFlow.
