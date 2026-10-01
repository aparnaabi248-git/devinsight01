"""Compare the live PostgreSQL schema with the SQLAlchemy metadata.

Exits non-zero when they diverge, so CI can use it as a schema-drift gate.

Checking only that a table and its columns *exist* is not enough: a migration that
declares a column `NOT NULL` where the ORM model says `nullable=True` produces a
working-looking database that then rejects every legitimate `NULL` insert. This gate
therefore compares each column's nullability and each table's primary key as well.
"""

from __future__ import annotations

import sys

import sqlalchemy as sa
from sqlalchemy import inspect

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.db.base import Base
from app.db.session import engine


def _pk_columns(inspector: sa.Inspector, table_name: str) -> set[str]:
    """`constrained_columns` is a list of column names, not objects."""
    return set(inspector.get_pk_constraint(table_name).get("constrained_columns") or ())


def main() -> int:
    with engine.connect() as connection:
        inspector = inspect(connection)
        problems: list[str] = []

        for table_name, table in sorted(Base.metadata.tables.items()):
            if not inspector.has_table(table_name):
                problems.append(f"{table_name}: missing in the database")
                continue

            db_columns = {c["name"]: c for c in inspector.get_columns(table_name)}
            orm_columns = {c.name: c for c in table.columns}

            for missing in sorted(set(orm_columns) - set(db_columns)):
                problems.append(f"{table_name}.{missing}: in ORM, missing in DB")
            for extra in sorted(set(db_columns) - set(orm_columns)):
                problems.append(f"{table_name}.{extra}: in DB, missing in ORM")

            # Nullability must agree, or the database rejects values the model allows.
            for name in sorted(set(db_columns) & set(orm_columns)):
                db_nullable = bool(db_columns[name].get("nullable", True))
                orm_nullable = bool(orm_columns[name].nullable)
                if db_nullable != orm_nullable:
                    problems.append(
                        f"{table_name}.{name}: nullability differs "
                        f"(DB {'NULL' if db_nullable else 'NOT NULL'} vs "
                        f"ORM {'nullable' if orm_nullable else 'not nullable'})"
                    )

            db_pk = _pk_columns(inspector, table_name)
            orm_pk = {c.name for c in table.primary_key.columns}
            if db_pk != orm_pk:
                problems.append(
                    f"{table_name}: primary key differs "
                    f"(DB {sorted(db_pk) or 'none'} vs ORM {sorted(orm_pk) or 'none'})"
                )

        db_tables = set(inspector.get_table_names()) - {"alembic_version"}
        for orphan in sorted(db_tables - set(Base.metadata.tables)):
            problems.append(f"{orphan}: table exists in DB but not in the ORM")

    if problems:
        print("SCHEMA DRIFT DETECTED")
        for problem in problems:
            print("  -", problem)
        return 1
    print(
        f"Schema in sync: {len(Base.metadata.tables)} tables verified "
        f"(columns, nullability and primary keys)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
