"""Generate ADMIN_PASSWORD_HASH.   Usage:  python -m backend.auth.hash_password"""
import getpass
import sys

from backend.auth.passwords import hash_password


def main() -> int:
    pw = getpass.getpass("Admin password (min 12 chars): ")
    if len(pw) < 12:
        print("Password too short.", file=sys.stderr)
        return 1
    if pw != getpass.getpass("Repeat: "):
        print("Passwords differ.", file=sys.stderr)
        return 1
    print("\nPut this in your .env (never commit it):\nADMIN_PASSWORD_HASH=" + hash_password(pw))
    return 0


if __name__ == "__main__":
    sys.exit(main())
