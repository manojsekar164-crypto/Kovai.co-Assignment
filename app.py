"""
app.py — Task Management Application (Phase 1)
================================================
Backend foundation using Flask + SQLite.

Architecture:
  - Database helpers  : get_db_connection(), init_db()
  - Validation helper : validate_task_input()
  - Routes            : /, /dashboard, /add-task, /update-task/<id>

Status values (enforced at DB and application level):
  Planned | In Progress | Complete
"""

import sqlite3
import os
import logging
from datetime import datetime, timezone

from flask import Flask, jsonify, request, g

# ---------------------------------------------------------------------------
# App & Logging Configuration
# ---------------------------------------------------------------------------

app = Flask(__name__)

# Secret key required by Flask for session management (replace in production
# via an environment variable).
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me-in-production")

# SQLite database file path. Can be overridden via the DATABASE_URL env var.
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

    The CHECK constraint on `status` is the last line of defence against
    invalid data reaching the database.
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
# Routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """
    GET /
    -----
    Health-check / landing endpoint.

    Returns a JSON payload confirming the service is running along with the
    current UTC timestamp.  This endpoint is intentionally lightweight so it
    can be used as a Render health-check URL.
    """
    return jsonify(
        {
            "service": "Task Management API",
            "phase": 1,
            "status": "running",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    ), 200


@app.route("/dashboard", methods=["GET"])
def dashboard():
    """
    GET /dashboard
    --------------
    Return all tasks ordered by creation date (newest first).

    Query Parameters (optional)
    ---------------------------
    status : Filter tasks by a specific status value.
             Example: /dashboard?status=In+Progress

    Response
    --------
    {
        "total": <int>,
        "tasks": [
            {
                "id":          <int>,
                "title":       <str>,
                "description": <str | null>,
                "status":      <str>,
                "created_at":  <str>
            },
            …
        ]
    }
    """
    status_filter = request.args.get("status", "").strip()

    try:
        db = get_db()

        if status_filter:
            # Validate the supplied filter value before querying.
            if status_filter not in VALID_STATUSES:
                return jsonify(
                    {
                        "error": (
                            f"Invalid status filter. "
                            f"Allowed values: {', '.join(sorted(VALID_STATUSES))}."
                        )
                    }
                ), 400

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
        return jsonify({"total": len(tasks), "tasks": tasks}), 200

    except sqlite3.Error as exc:
        logger.error("Dashboard DB error: %s", exc)
        return jsonify({"error": "Failed to retrieve tasks."}), 500


@app.route("/add-task", methods=["POST"])
def add_task():
    """
    POST /add-task
    --------------
    Create a new task.

    Accepts both JSON and form-encoded bodies.

    Required Fields
    ---------------
    title : str   — Task title (max 200 chars).

    Optional Fields
    ---------------
    description : str   — Free-text description (max 2 000 chars).
    status      : str   — One of 'Planned', 'In Progress', 'Complete'.
                          Defaults to 'Planned' if omitted.

    Response (201 Created)
    ----------------------
    {
        "message": "Task created successfully.",
        "task": { "id": <int>, "title": <str>, … }
    }
    """
    # Support both JSON and HTML form submissions.
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
        require_status=False,   # Status is optional; defaults to 'Planned'.
    )
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Normalise: default to 'Planned' if status was not supplied or is empty.
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

        return jsonify(
            {
                "message": "Task created successfully.",
                "task": {
                    "id": task_id,
                    "title": title,
                    "description": description,
                    "status": status,
                    "created_at": created_at,
                },
            }
        ), 201

    except sqlite3.IntegrityError as exc:
        # Catches CHECK constraint violations (invalid status at DB level).
        logger.warning("Integrity error on add-task: %s", exc)
        return jsonify({"error": "Data integrity error. Check your input values."}), 400
    except sqlite3.Error as exc:
        logger.error("DB error on add-task: %s", exc)
        return jsonify({"error": "Failed to create task."}), 500


@app.route("/update-task/<int:task_id>", methods=["POST"])
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

    Response (200 OK)
    -----------------
    {
        "message": "Task updated successfully.",
        "task": { "id": <int>, "title": <str>, … }
    }
    """
    if request.is_json:
        data = request.get_json(silent=True) or {}
    else:
        data = request.form.to_dict()

    # Extract updatable fields; `None` means "not supplied by caller".
    new_title = data.get("title", "").strip() or None
    new_description = data.get("description")         # May legitimately be ""
    new_status = data.get("status", "").strip() or None

    # At least one field must be provided.
    if new_title is None and new_description is None and new_status is None:
        return jsonify(
            {"error": "Provide at least one field to update: title, description, status."}
        ), 400

    # Validate only the fields that were actually supplied.
    is_valid, error_msg = validate_task_input(
        title=new_title,
        description=new_description,
        status=new_status,
        require_status=False,
    )
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    try:
        db = get_db()

        # Confirm the task exists before attempting an update.
        task_row = db.execute(
            "SELECT id, title, description, status, created_at FROM tasks WHERE id = ?;",
            (task_id,),
        ).fetchone()

        if task_row is None:
            return jsonify({"error": f"Task with id={task_id} not found."}), 404

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

        logger.info(
            "Task updated: id=%s status='%s'", task_id, merged_status
        )

        return jsonify(
            {
                "message": "Task updated successfully.",
                "task": {
                    "id": task_id,
                    "title": merged_title,
                    "description": merged_description,
                    "status": merged_status,
                    "created_at": task_row["created_at"],
                },
            }
        ), 200

    except sqlite3.IntegrityError as exc:
        logger.warning("Integrity error on update-task id=%s: %s", task_id, exc)
        return jsonify({"error": "Data integrity error. Check your input values."}), 400
    except sqlite3.Error as exc:
        logger.error("DB error on update-task id=%s: %s", task_id, exc)
        return jsonify({"error": "Failed to update task."}), 500


# ---------------------------------------------------------------------------
# Error Handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "Endpoint not found."}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "Method not allowed."}), 405


@app.errorhandler(500)
def internal_error(error):
    logger.error("Unhandled server error: %s", error)
    return jsonify({"error": "Internal server error."}), 500


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
