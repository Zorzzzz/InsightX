import secrets
import string

from database.db import bcrypt, login_manager
from database import user_repository

PASSWORD_MIN_LENGTH = 6
_LOGIN_MANAGER_CONFIGURED = False


class AuthServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_password(password: str, label: str = "Password") -> None:
    if len(password or "") < PASSWORD_MIN_LENGTH:
        raise AuthServiceError(
            f"{label} must be at least {PASSWORD_MIN_LENGTH} characters.",
            400,
        )


def hash_password(password: str) -> str:
    return bcrypt.generate_password_hash(password).decode("utf-8")


def verify_password(password_hash: str, password: str) -> bool:
    return bcrypt.check_password_hash(password_hash, password)


def configure_login_manager() -> None:
    global _LOGIN_MANAGER_CONFIGURED
    if _LOGIN_MANAGER_CONFIGURED:
        return

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return user_repository.get_user_by_id(int(user_id))
        except (TypeError, ValueError):
            return None

    _LOGIN_MANAGER_CONFIGURED = True


def register_user(
    *,
    email: str,
    password: str,
    username: str = "",
    full_name: str = "",
) -> object:
    normalized_email = normalize_email(email)
    derived_username = (username or normalized_email.split("@")[0]).strip()
    derived_full_name = (full_name or derived_username).strip()

    if not normalized_email or not password or not derived_username:
        raise AuthServiceError("Email, username, and password are required.", 400)

    validate_password(password)

    if user_repository.get_user_by_email(normalized_email):
        raise AuthServiceError("Email already registered.", 400)

    return user_repository.create_user(
        username=derived_username,
        full_name=derived_full_name,
        email=normalized_email,
        password_hash=hash_password(password),
    )


def authenticate_user(*, email: str, password: str) -> object:
    normalized_email = normalize_email(email)
    if not normalized_email or not password:
        raise AuthServiceError("Email and password are required.", 400)

    user = user_repository.get_user_by_email(normalized_email)
    if user is None or not verify_password(user.password, password):
        raise AuthServiceError("Invalid email or password.", 401)

    return user


def change_password_for_email(email: str, new_password: str, label: str = "Password") -> object:
    validate_password(new_password, label=label)

    user = user_repository.update_password(
        normalize_email(email),
        hash_password(new_password),
    )
    if user is None:
        raise AuthServiceError("User not found.", 404)
    return user


def generate_temporary_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
    ]
    while len(password) < length:
        password.append(secrets.choice(alphabet))
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def reset_password_for_email(email: str) -> tuple[object, str]:
    temporary_password = generate_temporary_password()
    user = change_password_for_email(email, temporary_password, label="Password")
    return user, temporary_password
