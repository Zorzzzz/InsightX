import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database.db import create_cli_app
from database import user_repository
from services.auth_service import AuthServiceError, change_password_for_email, reset_password_for_email


def _print_user(user) -> None:
    admin_flag = "yes" if user.is_admin else "no"
    full_name = user.full_name or "-"
    print(
        f"id={user.id} | email={user.email} | username={user.username} | "
        f"full_name={full_name} | admin={admin_flag}"
    )


def list_users_command() -> int:
    users = user_repository.list_users()
    if not users:
        print("No users found.")
        return 0

    for user in users:
        _print_user(user)
    return 0


def change_email_command(old_email: str, new_email: str) -> int:
    if user_repository.get_user_by_email(new_email):
        print(f"User with email {new_email} already exists.")
        return 1

    user = user_repository.update_email(old_email, new_email)
    if user is None:
        print(f"User with email {old_email} was not found.")
        return 1

    print(f"Updated email: {old_email} -> {user.email}")
    return 0


def change_password_command(email: str, new_password: str) -> int:
    try:
        change_password_for_email(email, new_password)
    except AuthServiceError as error:
        print(error.message)
        return 1

    print(f"Password updated for {email}.")
    return 0


def delete_user_command(email: str) -> int:
    if not user_repository.delete_user(email=email):
        print(f"User with email {email} was not found.")
        return 1

    print(f"Deleted user {email}.")
    return 0


def make_admin_command(email: str) -> int:
    user = user_repository.set_admin(email, True)
    if user is None:
        print(f"User with email {email} was not found.")
        return 1

    print(f"Granted admin access to {user.email}.")
    return 0


def reset_password_command(email: str) -> int:
    try:
        user, temporary_password = reset_password_for_email(email)
    except AuthServiceError as error:
        print(error.message)
        return 1

    print(f"Temporary password for {user.email}: {temporary_password}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="InsightX admin tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-users")

    change_email_parser = subparsers.add_parser("change-email")
    change_email_parser.add_argument("old_email")
    change_email_parser.add_argument("new_email")

    change_password_parser = subparsers.add_parser("change-password")
    change_password_parser.add_argument("email")
    change_password_parser.add_argument("new_password")

    delete_user_parser = subparsers.add_parser("delete-user")
    delete_user_parser.add_argument("email")

    make_admin_parser = subparsers.add_parser("make-admin")
    make_admin_parser.add_argument("email")

    reset_password_parser = subparsers.add_parser("reset-password")
    reset_password_parser.add_argument("email")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    app = create_cli_app()

    with app.app_context():
        if args.command == "list-users":
            return list_users_command()
        if args.command == "change-email":
            return change_email_command(args.old_email, args.new_email)
        if args.command == "change-password":
            return change_password_command(args.email, args.new_password)
        if args.command == "delete-user":
            return delete_user_command(args.email)
        if args.command == "make-admin":
            return make_admin_command(args.email)
        if args.command == "reset-password":
            return reset_password_command(args.email)

    print("Unknown command.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
