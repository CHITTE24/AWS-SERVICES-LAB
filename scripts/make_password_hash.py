"""Creates a password hash for ADMIN_PASSWORD_HASH in .env.

Usage (from the project folder, with the virtual environment activated):
    python scripts/make_password_hash.py
The password is typed invisibly and never saved. Only the hash is printed.
"""

import getpass  # Reads a password without showing it on screen.
import sys  # Lets the script exit with an error code.
from pathlib import Path  # Builds file paths.

# Lets this script import "auth" from the project folder (one level above scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth.passwords import hash_password  # noqa: E402  (this import must come after the path change above)


def main():
    password = getpass.getpass("New dashboard password: ")
    confirm = getpass.getpass("Type it again: ")

    # Simple safety checks before creating the hash.
    if password != confirm:
        print("The passwords do not match.")
        sys.exit(1)
    if len(password) < 12:
        print("Use at least 12 characters.")
        sys.exit(1)

    print("\nPaste this line into your .env file:\n")
    print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")


# This block runs only when the file is started directly, not when it is imported.
if __name__ == "__main__":
    main()
