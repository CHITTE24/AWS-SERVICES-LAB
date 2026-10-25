"""Hashes and checks passwords so the real password is never stored anywhere.

A stored hash looks like:  pbkdf2_sha256:600000:<salt>:<hash>
  - pbkdf2_sha256: the hashing method
  - 600000: how many times the hash is repeated (more = slower to guess)
  - salt: random text that makes identical passwords produce different hashes
"""

import hashlib  # Built-in hashing functions, including PBKDF2.
import hmac  # Provides compare_digest, a comparison safe against timing attacks.
import secrets  # Creates cryptographically secure random values.

# Repetitions for new hashes. 600,000 follows current OWASP guidance for PBKDF2-SHA256.
DEFAULT_ITERATIONS = 600000


def hash_password(password, iterations=DEFAULT_ITERATIONS):
    """Turns a plain password into a salted hash string that is safe to store in .env.

    password: the plain-text password.
    iterations: how many times to repeat the hash. Tests use a small number to run faster.
    Returns: a string in the format shown at the top of this file.
    """
    # A new random salt for every hash.
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256:{iterations}:{salt}:{digest.hex()}"


def verify_password(password, stored_hash):
    """Checks whether a typed password matches a stored hash.

    Returns True when it matches. Returns False when it does not match or the stored hash is malformed.
    """
    # Splits the stored text into its four parts.
    parts = stored_hash.split(":")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        return False

    algorithm, iterations_text, salt, expected_hex = parts
    # A non-numeric iteration count means the stored hash is broken.
    if not iterations_text.isdigit():
        return False

    # Hashes the typed password in exactly the same way as when the hash was created.
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations_text))

    # compare_digest takes the same time whether the first or last character differs, so it leaks nothing.
    return hmac.compare_digest(digest.hex(), expected_hex)
