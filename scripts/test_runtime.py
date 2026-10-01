"""Create/stop a disposable Unix-socket-only PostgreSQL test instance.

Only a fresh, explicitly selected directory is accepted. System services and
existing clusters are never touched. The environment file contains test secrets.
"""

import argparse
import os
import secrets
import shlex
import subprocess
from pathlib import Path

from cryptography.fernet import Fernet


def run(arguments):
    subprocess.run([str(value) for value in arguments], check=True, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bin", type=Path, default=Path("/opt/homebrew/opt/postgresql@16/bin"))
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "stop":
        if not (root / "isolated-test-instance").exists():
            raise SystemExit("Refusing to touch a directory without our test marker")
        run([args.bin / "pg_ctl", "-D", root / "pg", "-m", "fast", "stop"])
        print("Isolated test instance stopped; data retained at", root)
        return
    if root.exists():
        raise SystemExit("Choose a new directory; existing data will not be replaced")
    root.mkdir(mode=0o700)
    (root / "isolated-test-instance").write_text("Disposable instance for automated tests\n")
    socket = root / "socket"
    socket.mkdir(mode=0o700)
    run(
        [
            args.bin / "initdb",
            "-D",
            root / "pg",
            "-U",
            "bid_test_admin",
            "--auth-local=trust",
            "--auth-host=reject",
        ]
    )
    options = f"-k {socket} -h '' -p 55439"
    run(
        [
            args.bin / "pg_ctl",
            "-D",
            root / "pg",
            "-l",
            root / "postgres.log",
            "-o",
            options,
            "start",
        ]
    )
    run([args.bin / "createdb", "-h", socket, "-p", "55439", "-U", "bid_test_admin", "bid_test"])
    environment = {
        "BID_TEST_ADMIN_URL": f"postgresql+psycopg://bid_test_admin@/bid_test?host={socket}&port=55439",
        "BID_MIGRATION_DATABASE_URL": f"postgresql+psycopg://bid_test_admin@/bid_test?host={socket}&port=55439",
        "BID_DATABASE_URL": f"postgresql+psycopg://bid_app@/bid_test?host={socket}&port=55439",
        "BID_ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "BID_CLI_KEY": Fernet.generate_key().decode(),
        "BID_DATA_DIR": str(root / "files"),
        "BID_PASSWORD": secrets.token_urlsafe(24),
    }
    env_file = root / "environment.sh"
    descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        for key, value in environment.items():
            handle.write(f"export {key}={shlex.quote(value)}\n")
    print("Test environment file:", env_file)
    print("No TCP listener or system service was enabled.")


if __name__ == "__main__":
    main()
