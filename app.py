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
from flask_dance.contrib.google import make_google_blueprint, google
from flask_dance.consumer import oauth_authorized, oauth_error as oauth_error_signal


# ---------------------------------------------------------------------------
# App & Logging Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Secret key required by Flask for session management.
# Always set via the SECRET_KEY environment variable in production.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

# SQLite database file path.
DATABASE = os.environ.get("DATABASE_URL", "tasks.db")

# Allowed task status values — single source of truth.
VALID_STATUSES = {"Planned", "In Progress", "Complete"}

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


def init_db() -> None:
    """
    Initialise the SQLite database and create the `tasks` table if it does
    not already exist.

    Schema
    ------
    tasks(
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT    NOT NULL,
        description TEXT,
        status      TEXT    NOT NULL DEFAULT 'Planned'
                            CHECK(status IN ('Planned','In Progress','Complete')),
        created_at  TEXT    NOT NULL   -- ISO-8601 UTC timestamp
    )
    """
    logger.info("Initialising database at '%s'…", DATABASE)
    with sqlite3.connect(DATABASE) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                description TEXT,
                status      TEXT    NOT NULL DEFAULT 'Planned'
                                    CHECK(status IN ('Planned','In Progress','Complete')),
                created_at  TEXT    NOT NULL
            );
            """
        )
        conn.commit()
    logger.info("Database initialised successfully.")


# ---------------------------------------------------------------------------
# Statistics Helper
# ---------------------------------------------------------------------------

def get_task_stats() -> dict:
    """
    Query the database and return a dict of task counts.

    Returns
    -------
    {
        "total":       int,
        "planned":     int,
        "in_progress": int,
        "complete":    int,
    }

    Always reads the full dataset regardless of any active status filter,
    so the stat cards reflect the true global state at all times.
    """
    db = get_db()
    rows = db.execute(
        "SELECT status, COUNT(*) AS cnt FROM tasks GROUP BY status;"
    ).fetchall()
    count_map = {row["status"]: row["cnt"] for row in rows}
    return {
        "total":       sum(count_map.values()),
        "planned":     count_map.get("Planned", 0),
        "in_progress": count_map.get("In Progress", 0),
        "complete":    count_map.get("Complete", 0),
    }


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
    require_status: bool = False,
) -> tuple[bool, str]:
    """
    Validate task fields submitted from a form or JSON payload.

    Parameters
    ----------
    title          : Task title string (required when creating a task).
    description    : Optional free-text description.
    status         : Task status string.
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

    On first arrival after OAuth callback, `session["user"]` may not exist
    yet (Flask-Dance's redirect_to fires before we store user info).  In
    that case, fetch the profile from Google and cache it now.

    Query Parameters (optional)
    ---------------------------
    status : Filter the displayed task list by status value.
             Example: /dashboard?status=In+Progress
    """
    # First visit after OAuth — populate session["user"] from Google API.
    if "user" not in session:
        user = fetch_and_store_user()
        if user is None:
            # Token was stale/invalid — send back to login.
            flash("Authentication failed. Please sign in again.", "error")
            return redirect(url_for("login"))

    status_filter = request.args.get("status", "").strip()

    # Reject unknown filter values before hitting the DB.
    if status_filter and status_filter not in VALID_STATUSES:
        flash(
            f"Unknown status filter '{status_filter}'. Showing all tasks.",
            "warning",
        )
        return redirect(url_for("dashboard"))

    try:
        db = get_db()

        # --- Filtered task list ---
        if status_filter:
            rows = db.execute(
                "SELECT id, title, description, status, created_at "
                "FROM tasks WHERE status = ? ORDER BY created_at DESC;",
                (status_filter,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT id, title, description, status, created_at "
                "FROM tasks ORDER BY created_at DESC;"
            ).fetchall()

        tasks = [dict(row) for row in rows]

        # --- Statistics via helper (reads full dataset, ignores filter) ---
        stats = get_task_stats()

        # --- Retrieve any stashed form data (repopulate on validation error) ---
        form_data = session.pop("form_data", {})

        return render_template(
            "dashboard.html",
            tasks=tasks,
            stats=stats,
            current_filter=status_filter,
            form_data=form_data,
        )

    except sqlite3.Error as exc:
        logger.error("Dashboard DB error: %s", exc)
        flash("Unable to load tasks. Please try again.", "error")
        return render_template(
            "dashboard.html",
            tasks=[],
            stats={"total": 0, "planned": 0, "in_progress": 0, "complete": 0},
            current_filter="",
            form_data={},
        ), 500


@app.route("/add-task", methods=["POST"])
@login_required
def add_task():
    """
    POST /add-task
    --------------
    Create a new task. Accepts form-encoded or JSON bodies.

    Required Fields
    ---------------
    title : str — Task title (max 200 chars).

    Optional Fields
    ---------------
    description : str — Free-text description (max 2 000 chars).
    status      : str — One of 'Planned', 'In Progress', 'Complete'.
                        Defaults to 'Planned' if omitted.
    """
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    title = data.get("title", "").strip()
    description = data.get("description", "").strip() or None
    status = data.get("status", "Planned").strip()

    # --- Input validation ---
    is_valid, error_msg = validate_task_input(
        title=title,
        description=description,
        status=status,
        require_status=False,  # Status is optional; defaults to 'Planned'.
    )
    if not is_valid:
        session["form_data"] = {
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "status": data.get("status", "Planned"),
        }
        flash(error_msg, "error")
        return redirect(url_for("dashboard"))

    if not status:
        status = "Planned"

    created_at = datetime.now(timezone.utc).isoformat()

    try:
        db = get_db()
        cursor = db.execute(
            "INSERT INTO tasks (title, description, status, created_at) "
            "VALUES (?, ?, ?, ?);",
            (title, description, status, created_at),
        )
        db.commit()
        task_id = cursor.lastrowid

        logger.info("Task created: id=%s title='%s'", task_id, title)
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
    Update the status (and optionally title/description) of an existing task.

    Path Parameter
    --------------
    id : int — The task's primary key.

    Accepted Fields (at least one required)
    ----------------------------------------
    status      : str — New status value.
    title       : str — Updated title.
    description : str — Updated description.
    """
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    new_title = data.get("title", "").strip() or None
    new_description = data.get("description")  # May legitimately be ""
    new_status = data.get("status", "").strip() or None

    if new_title is None and new_description is None and new_status is None:
        flash("Provide at least one field to update.", "warning")
        return redirect(url_for("dashboard"))

    is_valid, error_msg = validate_task_input(
        title=new_title,
        description=new_description,
        status=new_status,
        require_status=False,
    )
    if not is_valid:
        flash(error_msg, "error")
        return redirect(url_for("dashboard"))

    try:
        db = get_db()

        task_row = db.execute(
            "SELECT id, title, description, status, created_at FROM tasks WHERE id = ?;",
            (task_id,),
        ).fetchone()

        if task_row is None:
            flash(f"Task #{task_id} not found.", "error")
            return redirect(url_for("dashboard"))

        # Merge incoming values with existing values (partial update semantics).
        merged_title = new_title if new_title is not None else task_row["title"]
        merged_description = (
            new_description if new_description is not None else task_row["description"]
        )
        merged_status = new_status if new_status is not None else task_row["status"]

        db.execute(
            "UPDATE tasks SET title = ?, description = ?, status = ? WHERE id = ?;",
            (merged_title, merged_description, merged_status, task_id),
        )
        db.commit()

        logger.info("Task updated: id=%s status='%s'", task_id, merged_status)
        flash(f"🔄 Task '{merged_title}' status updated to '{merged_status}'.", "success")
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
    Edit the title and/or description of an existing task.
    Status is intentionally NOT updated here; use /update-task/<id> for that.

    Required Fields
    ---------------
    title : str — Updated title (max 200 chars).

    Optional Fields
    ---------------
    description : str — Updated description (max 2 000 chars).
    """
    data = request.form.to_dict()
    new_title = data.get("title", "").strip()
    new_description = data.get("description", "").strip() or None

    if not new_title:
        flash("Title cannot be empty.", "error")
        return redirect(url_for("dashboard"))

    is_valid, error_msg = validate_task_input(
        title=new_title,
        description=new_description,
        status=None,
        require_status=False,
    )
    if not is_valid:
        flash(error_msg, "error")
        return redirect(url_for("dashboard"))

    try:
        db = get_db()

        task_row = db.execute(
            "SELECT id FROM tasks WHERE id = ?;", (task_id,)
        ).fetchone()

        if task_row is None:
            flash(f"Task #{task_id} not found.", "error")
            return redirect(url_for("dashboard"))

        db.execute(
            "UPDATE tasks SET title = ?, description = ? WHERE id = ?;",
            (new_title, new_description, task_id),
        )
        db.commit()

        logger.info("Task edited: id=%s new_title='%s'", task_id, new_title)
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

    Path Parameter
    --------------
    id : int — The task's primary key.
    """
    try:
        db = get_db()

        task_row = db.execute(
            "SELECT title FROM tasks WHERE id = ?;", (task_id,)
        ).fetchone()

        if task_row is None:
            flash(f"Task #{task_id} not found.", "error")
            return redirect(url_for("dashboard"))

        task_title = task_row["title"]
        db.execute("DELETE FROM tasks WHERE id = ?;", (task_id,))
        db.commit()

        logger.info("Task deleted: id=%s title='%s'", task_id, task_title)
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
