import argparse

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.user_auth import normalize_email


def set_admin(email: str, *, is_admin: bool) -> None:
    normalized = normalize_email(email)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == normalized))
        if user is None:
            raise SystemExit(f"User not found: {normalized}")
        if user.is_admin and not is_admin:
            raise SystemExit(
                "Use the protected admin dashboard to demote administrators."
            )
        user.is_admin = is_admin
        session.commit()
        action = "promoted" if is_admin else "updated"
        print(f"{normalized} {action} successfully")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap the first BarcodeNest administrator."
    )
    parser.add_argument("email", help="Existing BarcodeNest account email")
    args = parser.parse_args()
    set_admin(args.email, is_admin=True)


if __name__ == "__main__":
    main()
