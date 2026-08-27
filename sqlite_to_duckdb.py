
import argparse
import os
import sqlite3
import time

import duckdb
import pandas as pd

CHUNK = 250_000


def copy_via_extension(con, src_path, tables, counts) -> bool:
    """The fast path. Returns False if the add-on cannot be obtained."""
    try:
        con.execute("INSTALL sqlite")
        con.execute("LOAD sqlite")
        con.execute(f"ATTACH '{src_path}' AS src (TYPE sqlite, READ_ONLY)")
    except Exception as exc:
        print(f"  sqlite_scanner unavailable ({type(exc).__name__}), "
              "falling back to a chunked copy through Python\n")
        return False

    for t in tables:
        t0 = time.perf_counter()
        con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM src."{t}"')
        print(f"  {t:<24} {counts[t]:>12,} rows   {time.perf_counter() - t0:7.1f}s")
    con.execute("DETACH src")
    return True


def copy_via_chunks(con, src_path, tables, counts) -> None:
    """The slow path, needing nothing but sqlite3 and pandas."""
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    for t in tables:
        t0 = time.perf_counter()
        made = False
        for chunk in pd.read_sql(f'SELECT * FROM "{t}"', src, chunksize=CHUNK):
            # DuckDB reads the local variable `chunk` straight out of this
            # frame, so nothing is serialised on the way in.
            if made:
                con.execute(f'INSERT INTO "{t}" SELECT * FROM chunk')
            else:
                con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM chunk')
                made = True
        if not made:
            empty = pd.read_sql(f'SELECT * FROM "{t}" LIMIT 0', src)  # noqa: F841
            con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM empty')
        print(f"  {t:<24} {counts[t]:>12,} rows   {time.perf_counter() - t0:7.1f}s")
    src.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="path to the SQLite .db file")
    ap.add_argument("target", help="path to the .duckdb file to create")
    args = ap.parse_args()

    src_path = os.path.abspath(args.source)
    dst_path = os.path.abspath(args.target)

    if not os.path.exists(src_path):
        print(f"source not found: {src_path}")
        return 1
    if os.path.exists(dst_path):
        print(f"{dst_path} already exists. Delete it first if you want a rerun.")
        return 1

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts = {t: src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
              for t in tables}
    src.close()

    print(f"source : {src_path}")
    print(f"target : {dst_path}")
    print(f"tables : {len(tables)}, {sum(counts.values()):,} rows total\n")

    con = duckdb.connect(dst_path)
    started = time.perf_counter()
    if not copy_via_extension(con, src_path, tables, counts):
        copy_via_chunks(con, src_path, tables, counts)
    con.close()
    print(f"\ncopied in {time.perf_counter() - started:.1f}s")

    # Verify before reporting size. A smaller file that lost rows is not a win.
    con = duckdb.connect(dst_path, read_only=True)
    bad = []
    for t in tables:
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        if n != counts[t]:
            bad.append((t, counts[t], n))
    con.close()

    if bad:
        print("\nROW COUNTS DO NOT MATCH:")
        for t, a, b in bad:
            print(f"  {t}: sqlite {a:,} vs duckdb {b:,}")
        return 2
    print("row counts match on all tables")

    a, b = os.path.getsize(src_path), os.path.getsize(dst_path)
    print(f"\nSQLite : {a / 1e9:7.3f} GB")
    print(f"DuckDB : {b / 1e9:7.3f} GB")
    print(f"smaller by {a / b:.1f}x, freeing {(a - b) / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
