#!/usr/bin/env python3
"""Manage users for AA02 authentication.

Usage (inside the running container):
  docker exec aa02 python manage_users.py list
  docker exec aa02 python manage_users.py add <username> <password>
  docker exec aa02 python manage_users.py remove <username>

Or from the project directory (host):
  python manage_users.py list
  python manage_users.py add alice secretpass
  python manage_users.py remove alice
"""
import base64
import hashlib
import json
import os
import sys

USERS_FILE = os.getenv(
    "USERS_FILE",
    "/app/users.json" if os.path.isdir("/app") else "users.json",
)


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return base64.b64encode(salt).decode() + ":" + base64.b64encode(key).decode()


def _load() -> dict:
    if os.path.isfile(USERS_FILE):
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(users: dict) -> None:
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def cmd_add(username: str, password: str) -> None:
    users = _load()
    users[username] = _hash_password(password)
    _save(users)
    print(f"Added: {username}")


def cmd_remove(username: str) -> None:
    users = _load()
    if username in users:
        del users[username]
        _save(users)
        print(f"Removed: {username}")
    else:
        print(f"User not found: {username}", file=sys.stderr)
        sys.exit(1)


def cmd_list() -> None:
    users = _load()
    if users:
        for u in sorted(users):
            print(u)
    else:
        print("No users configured.")


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "add" and len(args) == 3:
        cmd_add(args[1], args[2])
    elif args and args[0] == "remove" and len(args) == 2:
        cmd_remove(args[1])
    elif args and args[0] == "list":
        cmd_list()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
