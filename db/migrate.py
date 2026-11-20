import argparse
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def _numbered_migrations(directory: Path) -> list[tuple[int, Path]]:
    migrations = {}
    for path in directory.glob("*.sql"):
        name = re.fullmatch(r"([0-9]+)_.+\.sql", path.name)
        if name is None:
            raise ValueError(
                f"Migration filename must start with a number: {path.name}"
            )
        version = int(name[1])
        if version < 1 or version in migrations:
            raise ValueError(f"Invalid or duplicate migration version: {path.name}")
        migrations[version] = path
    if 1 not in migrations:
        raise ValueError(f"Initial migration is missing from {directory}")
    return sorted(migrations.items())


def _already_applied(connection: sqlite3.Connection, version: int) -> bool:
    history = connection.execute(
        "SELECT 1 FROM main.sqlite_schema WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if history is None:
        # Migration 001 creates the history table itself.
        return False

    row = connection.execute(
        "SELECT version FROM main.schema_migrations "
        "WHERE version >= ? ORDER BY version LIMIT 1",
        (version,),
    ).fetchone()
    if row is None:
        return False
    if row[0] != version:
        raise ValueError(
            f"Migration {version} is missing before applied version {row[0]}"
        )
    return True


def _sql_statements(script: str) -> Iterator[str]:
    # execute() keeps the caller's transaction open. SQLite recognizes statement
    # boundaries, including semicolons within a CREATE TRIGGER body.
    start = 0
    for end, character in enumerate(script, start=1):
        if character == ";" and sqlite3.complete_statement(script[start:end]):
            yield script[start:end]
            start = end
    if script[start:].strip():
        yield script[start:]


def apply_migrations(
    connection: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR
) -> None:
    """Apply each pending migration and its version record in one transaction."""
    if connection.in_transaction:
        raise ValueError("Apply migrations on a connection with no active transaction")
    migrations = _numbered_migrations(migrations_dir)
    connection.execute("PRAGMA foreign_keys = ON")

    for version, path in migrations:
        try:
            with connection:
                # A second runner waits here before checking the applied version.
                connection.execute("BEGIN IMMEDIATE")
                if _already_applied(connection, version):
                    continue
                for statement in _sql_statements(path.read_text(encoding="utf-8")):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO main.schema_migrations (version) VALUES (?)",
                    (version,),
                )
        except (OSError, sqlite3.Error) as error:
            error.add_note(f"While applying {path.name}")
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply the SQLite database migrations."
    )
    parser.add_argument("database", type=Path, help="Path to the SQLite database file")
    args = parser.parse_args()

    with closing(sqlite3.connect(args.database)) as connection:
        apply_migrations(connection)


if __name__ == "__main__":
    main()
