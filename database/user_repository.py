from database.db import db
from database.models import User


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def create_user(
    *,
    username: str,
    full_name: str | None,
    email: str,
    password_hash: str,
    is_admin: bool = False,
) -> User:
    user = User(
        username=username.strip(),
        full_name=(full_name or "").strip() or None,
        email=_normalize_email(email),
        password=password_hash,
        is_admin=bool(is_admin),
    )
    db.session.add(user)
    db.session.commit()
    return user


def get_user_by_email(email: str) -> User | None:
    return User.query.filter_by(email=_normalize_email(email)).first()


def get_user_by_id(user_id: int) -> User | None:
    return db.session.get(User, user_id)


def update_email(old_email: str, new_email: str) -> User | None:
    user = get_user_by_email(old_email)
    if user is None:
        return None

    user.email = _normalize_email(new_email)
    db.session.commit()
    return user


def update_password(email: str, password_hash: str) -> User | None:
    user = get_user_by_email(email)
    if user is None:
        return None

    user.password = password_hash
    db.session.commit()
    return user


def update_full_name(user_id: int, full_name: str) -> User | None:
    user = get_user_by_id(user_id)
    if user is None:
        return None

    user.full_name = full_name.strip()
    db.session.commit()
    return user


def update_username(user_id: int, username: str) -> User | None:
    user = get_user_by_id(user_id)
    if user is None:
        return None
    user.username = username.strip()
    db.session.commit()
    return user


def update_user_fields(user_id: int, fields: dict) -> User | None:
    """Update arbitrary fields on a user. Keys: full_name, username, email, password."""
    user = get_user_by_id(user_id)
    if user is None:
        return None
    for key, value in fields.items():
        if hasattr(user, key):
            setattr(user, key, value)
    db.session.commit()
    return user


def set_admin(email: str, is_admin: bool = True) -> User | None:
    user = get_user_by_email(email)
    if user is None:
        return None

    user.is_admin = bool(is_admin)
    db.session.commit()
    return user


def delete_user(email: str | None = None, user_id: int | None = None) -> bool:
    user = None
    if email is not None:
        user = get_user_by_email(email)
    elif user_id is not None:
        user = get_user_by_id(user_id)

    if user is None:
        return False

    db.session.delete(user)
    db.session.commit()
    return True


def list_users() -> list[User]:
    return User.query.order_by(User.created_at.asc(), User.id.asc()).all()
