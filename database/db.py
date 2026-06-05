import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine

db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()


@event.listens_for(Engine, "connect")
def configure_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=MEMORY")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_APP_DATA_DIR = os.environ.get("BRIEFLY_DATA_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", BASE_DIR),
    "InsightX",
)

CONFIGURED_DATABASE_PATH = (
    os.environ.get("INSIGHTX_DATABASE_PATH")
    or os.environ.get("BRIEFLY_DATABASE_PATH")
)
LEGACY_DATABASE_PATH = os.path.join(BASE_DIR, "briefly.db")
LEGACY_APP_DATABASE_PATH = os.path.join(LEGACY_APP_DATA_DIR, "briefly.db")
LEGACY_APP_DATABASE_PATH_V2 = os.path.join(LEGACY_APP_DATA_DIR, "database.db")
DATABASE_PATH = os.path.abspath(CONFIGURED_DATABASE_PATH) if CONFIGURED_DATABASE_PATH else os.path.join(BASE_DIR, "database.db")
DATABASE_STEM, DATABASE_SUFFIX = os.path.splitext(DATABASE_PATH)
RECOVERY_DATABASE_PATH = f"{DATABASE_STEM}_recovered{DATABASE_SUFFIX or '.db'}"


def _database_is_healthy(path: str) -> bool:
    if not os.path.exists(path):
        return True

    connection = None
    try:
        connection = sqlite3.connect(path)
        result = connection.execute("PRAGMA quick_check(1)").fetchone()
        return bool(result) and result[0] == "ok"
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()


def _backup_unreadable_database(path: str) -> None:
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")

    for suffix in ("", "-journal", "-wal", "-shm"):
        source_path = f"{path}{suffix}"
        if not os.path.exists(source_path):
            continue

        backup_path = f"{source_path}.broken-{timestamp}"
        try:
            os.replace(source_path, backup_path)
            print(f"WARNING: Backed up unreadable database file to {backup_path}")
        except OSError as backup_error:
            print(f"WARNING: Could not back up {source_path}: {backup_error}")


def select_database_path() -> str:
    if os.path.exists(DATABASE_PATH):
        if _database_is_healthy(DATABASE_PATH):
            return DATABASE_PATH
        print("WARNING: Primary database is unreadable. Switching to recovery database.")
        _backup_unreadable_database(DATABASE_PATH)
        return RECOVERY_DATABASE_PATH

    if os.path.exists(RECOVERY_DATABASE_PATH) and _database_is_healthy(RECOVERY_DATABASE_PATH):
        print("WARNING: Primary database is missing. Reusing the recovery database.")
        return RECOVERY_DATABASE_PATH

    if os.path.exists(RECOVERY_DATABASE_PATH):
        if _database_is_healthy(RECOVERY_DATABASE_PATH):
            return RECOVERY_DATABASE_PATH
        print("WARNING: Recovery database is unreadable. Starting with a fresh primary database.")
        _backup_unreadable_database(RECOVERY_DATABASE_PATH)

    return DATABASE_PATH


ACTIVE_DATABASE_PATH = select_database_path()


def get_database_uri() -> str:
    return f"sqlite:///{Path(ACTIVE_DATABASE_PATH).as_posix()}"

def _create_sqlite_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(ACTIVE_DATABASE_PATH, check_same_thread=False)
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
    return connection

def init_extensions(app: Flask) -> None:
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", get_database_uri())
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {})
    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"].setdefault("creator", _create_sqlite_connection)

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login_page"


def _get_sqlite_table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    }


def _coerce_legacy_datetime(value):
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None

    text_value = str(value).strip()
    if not text_value:
        return None

    normalized = text_value.replace("T", " ")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    return None


def ensure_database_schema() -> None:
    inspector = inspect(db.engine)

    if "users" not in inspector.get_table_names():
        return

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    with db.engine.begin() as connection:
        if "full_name" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR(120)"))
            print("Database migrated: added users.full_name")
        if "is_admin" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
            print("Database migrated: added users.is_admin")


def _legacy_database_candidates() -> list[str]:
    candidates = [
        LEGACY_DATABASE_PATH,
        LEGACY_APP_DATABASE_PATH,
        LEGACY_APP_DATABASE_PATH_V2,
    ]
    active_path = os.path.abspath(ACTIVE_DATABASE_PATH)
    return [
        candidate
        for candidate in candidates
        if os.path.abspath(candidate) != active_path and os.path.exists(candidate)
    ]


def import_legacy_data_if_needed() -> None:
    from database.models import Summary, User

    if User.query.first() or Summary.query.first():
        return

    source_path = next(iter(_legacy_database_candidates()), None)
    if source_path is None:
        return

    if not _database_is_healthy(source_path):
        print(f"WARNING: Legacy database at {source_path} is unreadable. Skipping import.")
        return

    connection = sqlite3.connect(source_path)
    connection.row_factory = sqlite3.Row

    try:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "users" not in table_names and "summaries" not in table_names:
            return

        imported_user_ids: set[int] = set()
        imported_users = 0
        imported_summaries = 0

        user_columns = _get_sqlite_table_columns(connection, "users") if "users" in table_names else set()
        summary_columns = _get_sqlite_table_columns(connection, "summaries") if "summaries" in table_names else set()

        if {"id", "email", "password"} <= user_columns:
            for row in connection.execute("SELECT * FROM users ORDER BY id").fetchall():
                user_id = row["id"]
                email = (row["email"] or "").strip().lower()
                password_hash = row["password"]
                if user_id is None or not email or not password_hash:
                    continue

                username = (
                    row["username"]
                    if "username" in user_columns and row["username"]
                    else email.split("@")[0]
                )
                full_name = row["full_name"] if "full_name" in user_columns else None
                is_admin = bool(row["is_admin"]) if "is_admin" in user_columns else False
                created_at = _coerce_legacy_datetime(
                    row["created_at"] if "created_at" in user_columns else None
                ) or datetime.utcnow()

                db.session.add(User(
                    id=user_id,
                    username=username,
                    full_name=full_name,
                    email=email,
                    password=password_hash,
                    is_admin=is_admin,
                    created_at=created_at,
                ))
                imported_user_ids.add(user_id)
                imported_users += 1

            if imported_users:
                db.session.flush()

        if {"id", "user_id", "summary"} <= summary_columns:
            for row in connection.execute("SELECT * FROM summaries ORDER BY id").fetchall():
                user_id = row["user_id"]
                if user_id not in imported_user_ids and db.session.get(User, user_id) is None:
                    continue

                created_at = _coerce_legacy_datetime(
                    row["created_at"] if "created_at" in summary_columns else None
                ) or datetime.utcnow()

                db.session.add(Summary(
                    id=row["id"],
                    user_id=user_id,
                    input_text=row["input_text"] if "input_text" in summary_columns else None,
                    file_name=row["file_name"] if "file_name" in summary_columns else None,
                    summary=row["summary"],
                    category=(row["category"] if "category" in summary_columns else None) or "other",
                    stats_json=(row["stats_json"] if "stats_json" in summary_columns else None) or "{}",
                    created_at=created_at,
                ))
                imported_summaries += 1

        if imported_users or imported_summaries:
            db.session.commit()
            print(
                f"Imported {imported_users} user(s) and "
                f"{imported_summaries} summary record(s) from {os.path.basename(source_path)}."
            )
        else:
            db.session.rollback()
    except Exception as import_error:
        db.session.rollback()
        print(f"WARNING: Could not import legacy database contents: {import_error}")
    finally:
        connection.close()


def seed_admin_account() -> None:
    """Ensure the built-in admin account exists with the correct credentials."""
    from database.models import User

    ADMIN_EMAIL = "admin@gmail.com"
    ADMIN_PASSWORD = "admin2050"
    ADMIN_USERNAME = "admin"
    ADMIN_FULL_NAME = "Admin"

    existing = User.query.filter_by(email=ADMIN_EMAIL).first()
    if existing is None:
        new_hash = bcrypt.generate_password_hash(ADMIN_PASSWORD).decode("utf-8")
        admin = User(
            username=ADMIN_USERNAME,
            full_name=ADMIN_FULL_NAME,
            email=ADMIN_EMAIL,
            password=new_hash,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()
        print("Admin account created (admin@gmail.com).")
    else:
        changed = False
        if not existing.is_admin:
            existing.is_admin = True
            changed = True
        if existing.full_name != ADMIN_FULL_NAME and existing.full_name is None:
            existing.full_name = ADMIN_FULL_NAME
            changed = True
        if existing.username != ADMIN_USERNAME and existing.username is None:
            existing.username = ADMIN_USERNAME
            changed = True
        if changed:
            db.session.commit()
            print("Admin account updated.")


def initialize_database() -> None:
    from database import models  # noqa: F401

    db.create_all()
    ensure_database_schema()
    import_legacy_data_if_needed()
    seed_admin_account()
    print(f"Database ready ({os.path.basename(ACTIVE_DATABASE_PATH)})")


def create_cli_app() -> Flask:
    app = Flask("insightx_admin_tools")
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", secrets.token_hex(32))
    app.config["SQLALCHEMY_DATABASE_URI"] = get_database_uri()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    init_extensions(app)

    with app.app_context():
        initialize_database()

    return app
