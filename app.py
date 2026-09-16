"""
app.py — Task Management Application (Phase 4)
================================================
Phase 3: Complete task CRUD (create, view, edit, update status, delete).
Phase 4: Google OAuth 2.0 authentication via Flask-Dance.

Architecture:
  - Database helpers    : get_db(), init_db(), get_task_stats()
  - Auth helpers        : get_current_user(), login_required()
  - Validation helper   : validate_task_input()
  - Context processors  : inject_now(), inject_user()
  - Routes (public):
      GET  /                        → redirect to /dashboard or /login
      GET  /health                  → JSON health-check
      GET  /login                   → landing / login page
      POST /logout                  → clear session, redirect to /login
      GET  /login/google            → initiate Google OAuth (Flask-Dance)
      GET  /login/google/authorized → OAuth callback (Flask-Dance)
  - Routes (protected — @login_required):
      GET  /dashboard           → task list + stats
      POST /add-task            → create task
      POST /edit-task/<id>      → edit title & description
      POST /update-task/<id>    → update status
      POST /delete-task/<id>    → delete task

Status values (enforced at DB and application level):
  Planned | In Progress | Complete
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone
from functools import wraps

from dotenv import load_dotenv

# Load .env variables before any config reads (override existing environment variables).
load_dotenv(override=True)

from flask import (
    Flask, jsonify, request, g,
    render_template, redirect, url_for, flash, session,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized, oauth_error as oauth_error_signal


# ---------------------------------------------------------------------------
# App & Logging Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__)
# Support reverse proxy headers (X-Forwarded-Proto, X-Forwarded-For) on Render
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Secret key required by Flask for session management.
# Always set via the SECRET_KEY environment variable in production.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

# SQLite database file path.
DATABASE = os.environ.get("DATABASE_URL", "tasks.db")

# Allowed task status and priority values — single source of truth.
VALID_STATUSES = {"Planned", "In Progress", "Complete"}
VALID_PRIORITIES = {"Low", "Medium", "High"}

# Configure application-level logging.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google OAuth 2.0 — Flask-Dance Blueprint
# ---------------------------------------------------------------------------

google_bp = make_google_blueprint(
    client_id=os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ],
    # After a successful OAuth exchange, redirect the user to /dashboard.
    redirect_to="dashboard",
)
# Register at /login so the authorization URL is /login/google
# and the callback URL is /login/google/authorized.
app.register_blueprint(google_bp, url_prefix="/login")


# ---------------------------------------------------------------------------
# Database Helpers
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """
    Return a per-request SQLite connection stored on Flask's `g` proxy.

    Using `g` ensures a single connection is reused for the lifetime of one
    request and is properly closed afterwards (see `close_db` below).
    Row factory is set to `sqlite3.Row` so columns are accessible by name.
    """
    if "db" not in g:
        g.db = sqlite3.connect(
            DATABASE,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        g.db.row_factory = sqlite3.Row
        # Enforce foreign-key constraints and enable WAL mode for better
        # concurrency on read-heavy workloads.
        g.db.execute("PRAGMA foreign_keys = ON;")
        g.db.execute("PRAGMA journal_mode = WAL;")
    return g.db


@app.teardown_appcontext
def close_db(error=None) -> None:
    """Close the database connection at the end of every request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def migrate_db(conn: sqlite3.Connection) -> None:
    """
    Safely and idempotently upgrade the `tasks` table schema.

    - Inspects existing table columns via PRAGMA table_info(tasks).
    - If `user_id` is missing, adds it with DEFAULT 'legacy_user'.
    - If `user_email` is missing, adds it with DEFAULT 'legacy@example.com'.
    - If `priority` is missing, adds it with DEFAULT 'Medium'.
    - If `due_date` is missing, adds it as nullable TEXT.
    - Creates indexes for user lookups, status, priority, and due_date.
    - Idempotent: can run repeatedly without breaking existing databases.
    """
    try:
        cursor = conn.execute("PRAGMA table_info(tasks);")
        existing_cols = {row["name"] for row in cursor.fetchall()}

        if not existing_cols:
            return

        if "user_id" not in existing_cols:
            logger.info("Migrating database: adding 'user_id' column to tasks table.")
            conn.execute("ALTER TABLE tasks ADD COLUMN user_id TEXT NOT NULL DEFAULT 'legacy_user';")

        if "user_email" not in existing_cols:
            logger.info("Migrating database: adding 'user_email' column to tasks table.")
            conn.execute("ALTER TABLE tasks ADD COLUMN user_email TEXT NOT NULL DEFAULT 'legacy@example.com';")

        if "priority" not in existing_cols:
            logger.info("Migrating database: adding 'priority' column to tasks table.")
            conn.execute("ALTER TABLE tasks ADD COLUMN priority TEXT NOT NULL DEFAULT 'Medium';")

        if "due_date" not in existing_cols:
            logger.info("Migrating database: adding 'due_date' column to tasks table.")
            conn.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT;")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_priority ON tasks(user_id, priority);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user_due_date ON tasks(user_id, due_date);")

        # Database-level integrity triggers for priority on existing schemas
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_validate_priority_insert
            BEFORE INSERT ON tasks
            FOR EACH ROW
            WHEN NEW.priority NOT IN ('Low', 'Medium', 'High')
            BEGIN
                SELECT RAISE(ABORT, 'Invalid priority value. Must be Low, Medium, or High.');
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_validate_priority_update
            BEFORE UPDATE OF priority ON tasks
            FOR EACH ROW
            WHEN NEW.priority NOT IN ('Low', 'Medium', 'High')
            BEGIN
                SELECT RAISE(ABORT, 'Invalid priority value. Must be Low, Medium, or High.');
            END;
            """
        )

        conn.commit()
        logger.info("Database migration verified/completed.")
    except Exception as exc:
        logger.error("Database migration failure: %s", exc)
        raise


def init_db() -> None:
    """
    Initialise the SQLite database, create the `tasks` table if it does not
    already exist, and run idempotent schema migrations.

    Schema (Phase 7)
    ----------------
    tasks(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT    NOT NULL,
        user_email  TEXT    NOT NULL,
        title       TEXT    NOT NULL,
        description TEXT,
        status      TEXT    NOT NULL DEFAULT 'Planned'
                            CHECK(status IN ('Planned','In Progress','Complete')),
        priority    TEXT    NOT NULL DEFAULT 'Medium'
                            CHECK(priority IN ('Low','Medium','High')),
        due_date    TEXT,
        created_at  TEXT    NOT NULL   -- ISO-8601 UTC timestamp
    )
    """
    logger.info("Initialising database at '%s'…", DATABASE)
    with sqlite3.connect(DATABASE) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                user_email  TEXT    NOT NULL,
                title       TEXT    NOT NULL,
                description TEXT,
                status      TEXT    NOT NULL DEFAULT 'Planned'
                                    CHECK(status IN ('Planned','In Progress','Complete')),
                priority    TEXT    NOT NULL DEFAULT 'Medium'
                                    CHECK(priority IN ('Low','Medium','High')),
                due_date    TEXT,
                created_at  TEXT    NOT NULL
            );
            """
        )
        conn.commit()
        migrate_db(conn)
    logger.info("Database initialised successfully.")


# Ensure database tables and migrations are initialized on startup
with app.app_context():
    init_db()


# ---------------------------------------------------------------------------
# Statistics Helper & Productivity Insights (User-Scoped)
# ---------------------------------------------------------------------------

def get_task_stats(user_id: str) -> dict:
    """
    Query the database and return task counts and productivity insights scoped to the authenticated user.

    Parameters
    ----------
    user_id : str — The Google user ID of the authenticated user.

    Returns
    -------
    {
        "total":           int,
        "planned":         int,
        "in_progress":     int,
        "complete":        int,
        "completion_rate": int,
        "overdue":         int,
        "due_today":       int,
        "high_priority":   int,
        "today_str":       str,
    }
    """
    db = get_db()
    today_str = datetime.now().strftime("%Y-%m-%d")

    rows = db.execute(
        "SELECT status, COUNT(*) AS cnt FROM tasks WHERE user_id = ? GROUP BY status;",
        (user_id,),
    ).fetchall()
    count_map = {row["status"]: row["cnt"] for row in rows}
    total = sum(count_map.values())
    complete = count_map.get("Complete", 0)

    # Overdue tasks (non-complete tasks with due_date < today)
    overdue_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE user_id = ? AND status != 'Complete' "
        "AND due_date IS NOT NULL AND due_date != '' AND due_date < ?;",
        (user_id, today_str),
    ).fetchone()
    overdue = overdue_row["cnt"] if overdue_row else 0

    # Due today tasks (non-complete tasks with due_date == today)
    due_today_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE user_id = ? AND status != 'Complete' "
        "AND due_date = ?;",
        (user_id, today_str),
    ).fetchone()
    due_today = due_today_row["cnt"] if due_today_row else 0

    # High priority active tasks (non-complete tasks with priority == 'High')
    high_priority_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM tasks WHERE user_id = ? AND status != 'Complete' "
        "AND priority = 'High';",
        (user_id,),
    ).fetchone()
    high_priority = high_priority_row["cnt"] if high_priority_row else 0

    completion_rate = round((complete / total) * 100) if total > 0 else 0

    return {
        "total":           total,
        "planned":         count_map.get("Planned", 0),
        "in_progress":     count_map.get("In Progress", 0),
        "complete":        complete,
        "completion_rate": completion_rate,
        "overdue":         overdue,
        "due_today":       due_today,
        "high_priority":   high_priority,
        "today_str":       today_str,
    }


def enrich_task(task_dict: dict, today_str: str) -> dict:
    """
    Enrich a task dictionary with due_state and normalized display attributes.
    """
    due_date = task_dict.get("due_date")
    status = task_dict.get("status")
    priority = task_dict.get("priority")

    task_dict["priority"] = priority if priority in VALID_PRIORITIES else "Medium"

    if not due_date or not str(due_date).strip():
        task_dict["due_date"] = ""
        task_dict["due_state"] = "none"
        task_dict["due_label"] = ""
    elif status == "Complete":
        task_dict["due_state"] = "completed"
        task_dict["due_label"] = f"Done ({due_date})"
    elif str(due_date) < today_str:
        task_dict["due_state"] = "overdue"
        task_dict["due_label"] = f"Overdue ({due_date})"
    elif str(due_date) == today_str:
        task_dict["due_state"] = "today"
        task_dict["due_label"] = "Due Today"
    else:
        task_dict["due_state"] = "upcoming"
        task_dict["due_label"] = f"Due {due_date}"

    return task_dict


# ---------------------------------------------------------------------------
# Task Ownership Validation Helper
# ---------------------------------------------------------------------------

def verify_task_ownership(task_id: int, user_id: str) -> tuple[sqlite3.Row | None, str | None, int]:
    """
    Verify that a task exists and is owned by the current authenticated user.

    Parameters
    ----------
    task_id : int — Task primary key.
    user_id : str — Authenticated user's Google ID.

    Returns
    -------
    (task_row, error_message, status_code)
    - If valid: (task_row, None, 200)
    - If not found: (None, f"Task #{task_id} not found.", 404)
    - If owned by another user: (None, "You are not authorized to modify this task.", 403)
    """
    db = get_db()
    task_row = db.execute(
        "SELECT id, user_id, user_email, title, description, status, priority, due_date, created_at FROM tasks WHERE id = ?;",
        (task_id,),
    ).fetchone()

    if task_row is None:
        logger.warning("Task lookup failed: task #%s does not exist.", task_id)
        return None, f"Task #{task_id} not found.", 404

    if task_row["user_id"] != user_id:
        user_info = session.get("user", {})
        logger.warning(
            "SECURITY: Unauthorized task access attempt! User '%s' (%s) attempted to modify task #%s owned by '%s' (%s).",
            user_id,
            user_info.get("email", "unknown"),
            task_id,
            task_row["user_id"],
            dict(task_row).get("user_email", "unknown"),
        )
        return None, "You are not authorized to modify this task.", 403

    return task_row, None, 200


# ---------------------------------------------------------------------------
# Template Context Processors
# ---------------------------------------------------------------------------

@app.context_processor
def inject_now():
    """Inject the current UTC datetime into every template as `now`."""
    return {"now": datetime.now(timezone.utc)}


@app.context_processor
def inject_user():
    """
    Inject `current_user` into every template.

    Returns a dict with `logged_in: True` and user fields (name, email,
    picture, google_id) when authenticated, otherwise `logged_in: False`.
    """
    user = session.get("user")
    return {"current_user": user if user else {"logged_in": False}}


# ---------------------------------------------------------------------------
# Auth Helpers
# ---------------------------------------------------------------------------

def is_authenticated() -> bool:
    """
    Check if the current session is authenticated with Google OAuth.
    Returns True when Flask-Dance holds an authorized token AND user info is available.
    """
    if not google.authorized:
        return False
    if "user" not in session:
        user = fetch_and_store_user()
        return user is not None
    return True


def fetch_and_store_user() -> dict | None:
    """
    Fetch the user's profile from Google and cache it in the session.

    Called when authenticated via Google OAuth. If the token is invalid or
    the API call fails, the stale token is removed so `google.authorized`
    returns False on subsequent requests.

    Returns the user dict on success, or None on failure.
    """
    try:
        resp = google.get("/oauth2/v2/userinfo")
        if resp.ok:
            data = resp.json()
            user = {
                "logged_in":  True,
                "google_id":  data.get("id", ""),
                "name":       data.get("name", "User"),
                "email":      data.get("email", ""),
                "picture":    data.get("picture", ""),
            }
            session["user"] = user
            logger.info("User signed in: %s (%s)", user["name"], user["email"])
            return user
        else:
            logger.warning(
                "Google userinfo API returned HTTP %s — clearing stale token.",
                resp.status_code,
            )
    except Exception as exc:
        logger.error("Failed to fetch Google user info: %s", exc)

    # Token exists but is invalid — purge it so google.authorized → False.
    _clear_oauth_token()
    return None


def _clear_oauth_token() -> None:
    """Safely remove the Flask-Dance OAuth token from session storage."""
    try:
        del google_bp.token
    except Exception:
        pass
    for key in list(session.keys()):
        if "oauth" in key.lower() or "google" in key.lower():
            session.pop(key, None)


def login_required(f):
    """
    Route decorator that requires a fully authenticated Google session.

    Uses the dual-gate `is_authenticated()` check — not just
    `google.authorized` — so stale or corrupt tokens can never grant
    access to protected routes.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_authenticated():
            flash("Please sign in with Google to access the dashboard.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# OAuth Error Signal Handler
# ---------------------------------------------------------------------------

@oauth_error_signal.connect_via(google_bp)
def handle_google_oauth_error(blueprint, message, response):
    """
    Handle Flask-Dance OAuthError signals gracefully.

    Triggered automatically when the OAuth token exchange fails (e.g. the
    user denies access, or the client credentials are wrong). Logs the error
    and shows a user-friendly flash message.
    """
    logger.error(
        "Google OAuth error [blueprint=%s]: %s | response=%s",
        blueprint.name, message, response,
    )
    flash(
        "Google sign-in failed. Please try again or contact support.",
        "error",
    )


@oauth_authorized.connect_via(google_bp)
def google_logged_in(blueprint, token):
    """
    Called automatically upon successful OAuth authorization before redirect.
    Fetches user profile and caches it in session['user'].
    """
    if not token:
        flash("Failed to sign in with Google.", "error")
        return False

    resp = blueprint.session.get("/oauth2/v2/userinfo")
    if not resp.ok:
        flash("Failed to fetch user information from Google.", "error")
        return False

    data = resp.json()
    session["user"] = {
        "logged_in": True,
        "google_id": data.get("id", ""),
        "name": data.get("name", "User"),
        "email": data.get("email", ""),
        "picture": data.get("picture", ""),
    }
    logger.info("User signed in via OAuth signal: %s (%s)", session["user"]["name"], session["user"]["email"])


# ---------------------------------------------------------------------------
# Validation Helper
# ---------------------------------------------------------------------------

def validate_task_input(
    title: str | None,
    description: str | None,
    status: str | None,
    priority: str | None = None,
    due_date: str | None = None,
    require_status: bool = False,
) -> tuple[bool, str]:
    """
    Validate task fields submitted from a form or JSON payload.

    Parameters
    ----------
    title          : Task title string (required when creating a task).
    description    : Optional free-text description.
    status         : Task status string.
    priority       : Optional task priority ('Low', 'Medium', 'High').
    due_date       : Optional ISO date string ('YYYY-MM-DD').
    require_status : When True, `status` must be provided and valid.

    Returns
    -------
    (is_valid, error_message)
    """
    if title is not None:
        title = title.strip()
        if not title:
            return False, "Title must not be empty."
        if len(title) > 200:
            return False, "Title must be 200 characters or fewer."

    if description is not None and len(description) > 2000:
        return False, "Description must be 2 000 characters or fewer."

    if require_status or status is not None:
        if not status or status.strip() not in VALID_STATUSES:
            return False, (
                f"Invalid status. Allowed values: {', '.join(sorted(VALID_STATUSES))}."
            )

    if priority is not None and str(priority).strip():
        if str(priority).strip() not in VALID_PRIORITIES:
            return False, (
                f"Invalid priority. Allowed values: {', '.join(sorted(VALID_PRIORITIES))}."
            )

    if due_date is not None and str(due_date).strip():
        try:
            datetime.strptime(str(due_date).strip(), "%Y-%m-%d")
        except ValueError:
            return False, "Invalid due date format. Expected YYYY-MM-DD."

    return True, ""


# ---------------------------------------------------------------------------
# Routes — Public (no authentication required)
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """
    GET /
    -----
    Authenticated users go to /dashboard; unauthenticated users see the login page.
    """
    if is_authenticated():
        return redirect(url_for("dashboard"))
    if google.authorized and "user" not in session:
        _clear_oauth_token()
        session.clear()
    return render_template("login.html")


@app.route("/login", methods=["GET"])
def login():
    """
    GET /login
    ----------
    Render the branded landing page with a Google Sign-In button.

    If the session contains a stale OAuth token (google.authorized is True
    but no verified user data in session), clear it here so the user gets a
    clean login page instead of an infinite redirect loop.
    """
    if is_authenticated():
        return redirect(url_for("dashboard"))

    # Purge any stale/orphaned OAuth token that survived a previous session.
    if google.authorized and "user" not in session:
        logger.info("Clearing stale OAuth token on /login.")
        _clear_oauth_token()
        session.clear()

    return render_template("login.html")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    """
    GET|POST /logout
    ----------------
    Sign the user out completely:
      1. Attempt to revoke the Google OAuth access token.
      2. Delete the token from Flask-Dance's session storage.
      3. Clear the entire Flask session (removes cached user info).
      4. Redirect to /login.

    Accepts GET as well so the user can type /logout in the address bar.
    """
    if google.authorized:
        # Attempt graceful token revocation at Google's endpoint.
        try:
            token = google_bp.token
            if token and token.get("access_token"):
                google.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token["access_token"]},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            logger.warning("Token revocation skipped: %s", exc)
        # Remove the OAuth token from Flask-Dance's storage.
        _clear_oauth_token()

    session.clear()
    flash("You have been signed out successfully.", "success")
    return redirect(url_for("login"))


@app.route("/health", methods=["GET"])
def health():
    """
    GET /health
    -----------
    Lightweight JSON health-check used by Render and monitoring tools.
    """
    return jsonify(
        {
            "service": "Task Management API",
            "phase": 4,
            "status": "running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    ), 200


# ---------------------------------------------------------------------------
# Routes — Protected (Google login required)
# ---------------------------------------------------------------------------

@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    """
    GET /dashboard
    --------------
    Render the main dashboard page.
    Displays ONLY tasks and statistics belonging to the currently authenticated user.

    Query Parameters (optional)
    ---------------------------
    status   : Filter by status ('Planned', 'In Progress', 'Complete')
    priority : Filter by priority ('Low', 'Medium', 'High')
    due      : Filter by due date state ('overdue', 'today', 'upcoming')
    q        : Search query across title and description
    sort     : Sort order ('newest', 'oldest', 'priority', 'due_date', 'status')
    """
    # First visit after OAuth — populate session["user"] from Google API if missing.
    if "user" not in session:
        user = fetch_and_store_user()
        if user is None:
            flash("Authentication failed. Please sign in again.", "error")
            return redirect(url_for("login"))

    current_user = session.get("user")
    if not current_user or not current_user.get("google_id"):
        logger.warning("SECURITY: Dashboard accessed without valid google_id.")
        flash("Please sign in with Google to access the dashboard.", "warning")
        return redirect(url_for("login"))

    user_id = current_user["google_id"]
    today_str = datetime.now().strftime("%Y-%m-%d")

    status_filter = request.args.get("status", "").strip()
    priority_filter = request.args.get("priority", "").strip()
    due_filter = request.args.get("due", "").strip().lower()
    search_query = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "newest").strip().lower()

    # Gracefully sanitize filter inputs
    if status_filter not in VALID_STATUSES:
        status_filter = ""
    if priority_filter not in VALID_PRIORITIES:
        priority_filter = ""
    if due_filter not in {"overdue", "today", "upcoming"}:
        due_filter = ""

    try:
        db = get_db()

        # Build parameterized query scoped strictly to the authenticated user
        conditions = ["user_id = ?"]
        params = [user_id]

        if search_query:
            conditions.append("(title LIKE ? OR description LIKE ?)")
            params.extend([f"%{search_query}%", f"%{search_query}%"])

        if status_filter:
            conditions.append("status = ?")
            params.append(status_filter)

        if priority_filter:
            conditions.append("priority = ?")
            params.append(priority_filter)

        if due_filter == "overdue":
            conditions.append("status != 'Complete' AND due_date IS NOT NULL AND due_date != '' AND due_date < ?")
            params.append(today_str)
        elif due_filter == "today":
            conditions.append("status != 'Complete' AND due_date = ?")
            params.append(today_str)
        elif due_filter == "upcoming":
            conditions.append("status != 'Complete' AND due_date IS NOT NULL AND due_date != '' AND due_date > ?")
            params.append(today_str)

        where_clause = " WHERE " + " AND ".join(conditions)

        # Dynamic Sorting
        if sort_by == "oldest":
            order_clause = " ORDER BY created_at ASC"
        elif sort_by == "priority":
            order_clause = (
                " ORDER BY CASE priority "
                "WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END, "
                "created_at DESC"
            )
        elif sort_by == "due_date":
            order_clause = (
                " ORDER BY CASE WHEN due_date IS NULL OR due_date = '' THEN 1 ELSE 0 END, "
                "due_date ASC, created_at DESC"
            )
        elif sort_by == "status":
            order_clause = (
                " ORDER BY CASE status "
                "WHEN 'Planned' THEN 1 WHEN 'In Progress' THEN 2 WHEN 'Complete' THEN 3 ELSE 4 END, "
                "created_at DESC"
            )
        else:
            sort_by = "newest"
            order_clause = " ORDER BY created_at DESC"

        sql = (
            f"SELECT id, user_id, user_email, title, description, status, priority, due_date, created_at "
            f"FROM tasks {where_clause} {order_clause};"
        )
        rows = db.execute(sql, params).fetchall()
        tasks = [enrich_task(dict(row), today_str) for row in rows]

        # --- Statistics & Productivity Insights scoped to this user only ---
        stats = get_task_stats(user_id)

        # --- Retrieve any stashed form data (repopulate on validation error) ---
        form_data = session.pop("form_data", {})

        return render_template(
            "dashboard.html",
            tasks=tasks,
            stats=stats,
            current_filter=status_filter,
            current_priority=priority_filter,
            current_due=due_filter,
            search_query=search_query,
            sort_by=sort_by,
            form_data=form_data,
        )

    except sqlite3.Error as exc:
        logger.error("Dashboard DB error for user '%s': %s", user_id, exc)
        flash("Unable to load tasks. Please try again.", "error")
        return render_template(
            "dashboard.html",
            tasks=[],
            stats={"total": 0, "planned": 0, "in_progress": 0, "complete": 0, "completion_rate": 0, "overdue": 0, "due_today": 0, "high_priority": 0},
            current_filter="",
            current_priority="",
            current_due="",
            search_query="",
            sort_by="newest",
            form_data={},
        ), 500


@app.route("/add-task", methods=["POST"])
@login_required
def add_task():
    """
    POST /add-task
    --------------
    Create a new task owned by the currently authenticated user.
    `user_id` and `user_email` are automatically extracted from session.

    Required Fields
    ---------------
    title : str — Task title (max 200 chars).

    Optional Fields
    ---------------
    description : str — Free-text description (max 2 000 chars).
    status      : str — One of 'Planned', 'In Progress', 'Complete'. Defaults to 'Planned'.
    priority    : str — One of 'Low', 'Medium', 'High'. Defaults to 'Medium'.
    due_date    : str — ISO date 'YYYY-MM-DD' or empty.
    """
    current_user = session.get("user")
    if not current_user or not current_user.get("google_id"):
        logger.warning("SECURITY: add-task attempt without valid user in session.")
        flash("Please sign in with Google to create tasks.", "error")
        return redirect(url_for("login"))

    user_id = current_user["google_id"]
    user_email = current_user.get("email", "")

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    title = data.get("title", "").strip()
    description = data.get("description", "").strip() or None
    status = data.get("status", "Planned").strip() or "Planned"
    priority = data.get("priority", "Medium").strip() or "Medium"
    due_date = data.get("due_date", "").strip() or None

    # --- Input validation ---
    is_valid, error_msg = validate_task_input(
        title=title,
        description=description,
        status=status,
        priority=priority,
        due_date=due_date,
        require_status=False,
    )
    if not is_valid:
        session["form_data"] = {
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "status": status,
            "priority": priority,
            "due_date": data.get("due_date", ""),
        }
        flash(error_msg, "error")
        return redirect(url_for("dashboard"))

    if not status or status not in VALID_STATUSES:
        status = "Planned"
    if not priority or priority not in VALID_PRIORITIES:
        priority = "Medium"

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        db = get_db()
        cursor = db.execute(
            "INSERT INTO tasks (user_id, user_email, title, description, status, priority, due_date, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?);",
            (user_id, user_email, title, description, status, priority, due_date, created_at),
        )
        db.commit()
        task_id = cursor.lastrowid

        logger.info("Task created: id=%s title='%s' user_id='%s' priority='%s'", task_id, title, user_id, priority)
        flash(f"✅ Task '{title}' created successfully!", "success")
        return redirect(url_for("dashboard"))

    except sqlite3.IntegrityError as exc:
        logger.warning("Integrity error on add-task: %s", exc)
        flash("Data integrity error. Please check your input values.", "error")
        return redirect(url_for("dashboard"))
    except sqlite3.Error as exc:
        logger.error("DB error on add-task: %s", exc)
        flash("Failed to create task. Please try again.", "error")
        return redirect(url_for("dashboard"))


@app.route("/update-task/<int:task_id>", methods=["POST"])
@login_required
def update_task(task_id: int):
    """
    POST /update-task/<id>
    ----------------------
    Update status, title, description, priority, or due_date of an existing task.
    Enforces strict task ownership check against session['user']['google_id'].
    Returns HTTP 403 if user is not authorized.
    """
    current_user = session.get("user")
    if not current_user or not current_user.get("google_id"):
        logger.warning("SECURITY: Unauthorized update-task attempt: missing session user.")
        flash("Please sign in with Google to modify tasks.", "error")
        return redirect(url_for("login"))

    user_id = current_user["google_id"]
    task_row, err_msg, status_code = verify_task_ownership(task_id, user_id)

    if task_row is None:
        flash(err_msg, "error")
        if request.is_json:
            return jsonify({"error": err_msg}), status_code
        if status_code == 403:
            db = get_db()
            today_str = datetime.now().strftime("%Y-%m-%d")
            user_tasks = [
                enrich_task(dict(r), today_str) for r in db.execute(
                    "SELECT id, user_id, user_email, title, description, status, priority, due_date, created_at "
                    "FROM tasks WHERE user_id = ? ORDER BY created_at DESC;",
                    (user_id,),
                ).fetchall()
            ]
            return render_template(
                "dashboard.html",
                tasks=user_tasks,
                stats=get_task_stats(user_id),
                current_filter="",
                current_priority="",
                current_due="",
                search_query="",
                sort_by="newest",
                form_data={},
            ), 403
        return redirect(url_for("dashboard"))

    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    new_title = data.get("title", "").strip() or None
    new_description = data.get("description")  # May legitimately be ""
    new_status = data.get("status", "").strip() or None
    new_priority = data.get("priority", "").strip() or None
    new_due_date = data.get("due_date", "").strip() if "due_date" in data else None
    if new_due_date == "":
        new_due_date = None

    if new_title is None and new_description is None and new_status is None and new_priority is None and "due_date" not in data:
        flash("Provide at least one field to update.", "warning")
        return redirect(url_for("dashboard"))

    is_valid, error_msg = validate_task_input(
        title=new_title,
        description=new_description,
        status=new_status,
        priority=new_priority,
        due_date=new_due_date,
        require_status=False,
    )
    if not is_valid:
        flash(error_msg, "error")
        return redirect(url_for("dashboard"))

    try:
        db = get_db()
        merged_title = new_title if new_title is not None else task_row["title"]
        merged_description = (
            new_description if new_description is not None else task_row["description"]
        )
        merged_status = new_status if (new_status and new_status in VALID_STATUSES) else task_row["status"]
        merged_priority = new_priority if (new_priority and new_priority in VALID_PRIORITIES) else (dict(task_row).get("priority") or "Medium")
        merged_due_date = new_due_date if "due_date" in data else dict(task_row).get("due_date")

        db.execute(
            "UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?, due_date = ? "
            "WHERE id = ? AND user_id = ?;",
            (merged_title, merged_description, merged_status, merged_priority, merged_due_date, task_id, user_id),
        )
        db.commit()

        logger.info("Task updated: id=%s user_id='%s' status='%s' priority='%s'", task_id, user_id, merged_status, merged_priority)
        flash(f"🔄 Task '{merged_title}' updated successfully.", "success")
        return redirect(url_for("dashboard"))

    except sqlite3.IntegrityError as exc:
        logger.warning("Integrity error on update-task id=%s: %s", task_id, exc)
        flash("Data integrity error. Please check your input values.", "error")
        return redirect(url_for("dashboard"))
    except sqlite3.Error as exc:
        logger.error("DB error on update-task id=%s: %s", task_id, exc)
        flash("Failed to update task. Please try again.", "error")
        return redirect(url_for("dashboard"))


@app.route("/edit-task/<int:task_id>", methods=["POST"])
@login_required
def edit_task(task_id: int):
    """
    POST /edit-task/<id>
    --------------------
    Edit the title, description, status, priority, and due_date of an existing task.
    Enforces strict task ownership check against session['user']['google_id'].
    Returns HTTP 403 if user is not authorized.
    """
    current_user = session.get("user")
    if not current_user or not current_user.get("google_id"):
        logger.warning("SECURITY: Unauthorized edit-task attempt: missing session user.")
        flash("Please sign in with Google to edit tasks.", "error")
        return redirect(url_for("login"))

    user_id = current_user["google_id"]
    task_row, err_msg, status_code = verify_task_ownership(task_id, user_id)

    if task_row is None:
        flash(err_msg, "error")
        if request.is_json:
            return jsonify({"error": err_msg}), status_code
        if status_code == 403:
            db = get_db()
            today_str = datetime.now().strftime("%Y-%m-%d")
            user_tasks = [
                enrich_task(dict(r), today_str) for r in db.execute(
                    "SELECT id, user_id, user_email, title, description, status, priority, due_date, created_at "
                    "FROM tasks WHERE user_id = ? ORDER BY created_at DESC;",
                    (user_id,),
                ).fetchall()
            ]
            return render_template(
                "dashboard.html",
                tasks=user_tasks,
                stats=get_task_stats(user_id),
                current_filter="",
                current_priority="",
                current_due="",
                search_query="",
                sort_by="newest",
                form_data={},
            ), 403
        return redirect(url_for("dashboard"))

    data = request.form.to_dict()
    new_title = data.get("title", "").strip()
    new_description = data.get("description", "").strip() or None
    new_status = data.get("status", "").strip() or None
    new_priority = data.get("priority", "").strip() or None
    new_due_date = data.get("due_date", "").strip() if "due_date" in data else None
    if new_due_date == "":
        new_due_date = None

    if not new_title:
        flash("Title cannot be empty.", "error")
        return redirect(url_for("dashboard"))

    is_valid, error_msg = validate_task_input(
        title=new_title,
        description=new_description,
        status=new_status,
        priority=new_priority,
        due_date=new_due_date,
        require_status=False,
    )
    if not is_valid:
        flash(error_msg, "error")
        return redirect(url_for("dashboard"))

    try:
        db = get_db()
        merged_title = new_title
        merged_description = new_description
        merged_status = new_status if (new_status and new_status in VALID_STATUSES) else task_row["status"]
        merged_priority = new_priority if (new_priority and new_priority in VALID_PRIORITIES) else (dict(task_row).get("priority") or "Medium")
        merged_due_date = new_due_date if "due_date" in data else dict(task_row).get("due_date")

        db.execute(
            "UPDATE tasks SET title = ?, description = ?, status = ?, priority = ?, due_date = ? "
            "WHERE id = ? AND user_id = ?;",
            (merged_title, merged_description, merged_status, merged_priority, merged_due_date, task_id, user_id),
        )
        db.commit()

        logger.info("Task edited: id=%s user_id='%s' new_title='%s' priority='%s'", task_id, user_id, new_title, merged_priority)
        flash(f"✏️ Task '{new_title}' updated successfully.", "success")
        return redirect(url_for("dashboard"))

    except sqlite3.Error as exc:
        logger.error("DB error on edit-task id=%s: %s", task_id, exc)
        flash("Failed to edit task. Please try again.", "error")
        return redirect(url_for("dashboard"))


@app.route("/delete-task/<int:task_id>", methods=["POST"])
@login_required
def delete_task(task_id: int):
    """
    POST /delete-task/<id>
    ----------------------
    Permanently delete a task from the database.
    Enforces strict task ownership check against session['user']['google_id'].
    Returns HTTP 403 if user is not authorized.
    """
    current_user = session.get("user")
    if not current_user or not current_user.get("google_id"):
        logger.warning("SECURITY: Unauthorized delete-task attempt: missing session user.")
        flash("Please sign in with Google to delete tasks.", "error")
        return redirect(url_for("login"))

    user_id = current_user["google_id"]
    task_row, err_msg, status_code = verify_task_ownership(task_id, user_id)

    if task_row is None:
        flash(err_msg, "error")
        if request.is_json:
            return jsonify({"error": err_msg}), status_code
        if status_code == 403:
            db = get_db()
            today_str = datetime.now().strftime("%Y-%m-%d")
            user_tasks = [
                enrich_task(dict(r), today_str) for r in db.execute(
                    "SELECT id, user_id, user_email, title, description, status, priority, due_date, created_at "
                    "FROM tasks WHERE user_id = ? ORDER BY created_at DESC;",
                    (user_id,),
                ).fetchall()
            ]
            return render_template(
                "dashboard.html",
                tasks=user_tasks,
                stats=get_task_stats(user_id),
                current_filter="",
                current_priority="",
                current_due="",
                search_query="",
                sort_by="newest",
                form_data={},
            ), 403
        return redirect(url_for("dashboard"))

    try:
        db = get_db()
        task_title = task_row["title"]
        db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?;", (task_id, user_id))
        db.commit()

        logger.info("Task deleted: id=%s user_id='%s' title='%s'", task_id, user_id, task_title)
        flash(f"🗑️ Task '{task_title}' deleted.", "success")
        return redirect(url_for("dashboard"))

    except sqlite3.Error as exc:
        logger.error("DB error on delete-task id=%s: %s", task_id, exc)
        flash("Failed to delete task. Please try again.", "error")
        return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(error):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"error": "Endpoint not found."}), 404
    flash("Page not found.", "error")
    destination = "dashboard" if is_authenticated() else "login"
    return redirect(url_for(destination))


@app.errorhandler(405)
def method_not_allowed(error):
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"error": "Method not allowed."}), 405
    flash("That action is not allowed.", "error")
    destination = "dashboard" if is_authenticated() else "login"
    return redirect(url_for(destination))


@app.errorhandler(500)
def internal_error(error):
    logger.error("Unhandled server error: %s", error)
    if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
        return jsonify({"error": "Internal server error."}), 500
    flash("An unexpected error occurred. Please try again.", "error")
    destination = "dashboard" if is_authenticated() else "login"
    return redirect(url_for(destination))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Initialise the database (creates tables if they don't exist).
    init_db()

    # Run the development server.
    # In production (Render) gunicorn is used instead (see Procfile).
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
