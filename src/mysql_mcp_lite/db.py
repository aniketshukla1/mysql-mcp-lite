"""Shared core for the MCP server and the CLI: connection, read-only guard, compact results."""

import json
import os
import re

import pymysql
from mcp.server.mcpserver.exceptions import ToolError

ALLOW_WRITE = os.getenv("MYSQL_ALLOW_WRITE", "").lower() in ("1", "true", "yes")
MAX_ROWS = int(os.getenv("MYSQL_MAX_ROWS", "500"))
TIMEOUT_MS = int(os.getenv("MYSQL_TIMEOUT_MS", "30000"))

# Read-only mode has three layers. These checks reject the obvious; the server-side
# READ ONLY transaction blocks DML they miss; a SELECT-only MySQL user is the real boundary.
READ_KEYWORDS = {"select", "show", "describe", "desc", "explain", "with", "table", "values"}
COMMENTS = re.compile(r"/\*.*?\*/|(?:--\s|#)[^\n]*", re.S)
FILE_WRITE = re.compile(r"outfile|dumpfile", re.I)  # raw substring: comments can't split a keyword


class QueryError(ToolError):
    """A failure the caller must read. MCP passes a ToolError's text to the model; the CLI prints it."""


def connect(database: str | None = None) -> pymysql.Connection:
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database or os.getenv("MYSQL_DATABASE") or None,
        charset="utf8mb4",
        connect_timeout=10,
        init_command=f"SET SESSION MAX_EXECUTION_TIME={TIMEOUT_MS}",  # bounds SELECTs only
    )


def run(sql: str, args=None, database: str | None = None, read_only: bool = True) -> dict:
    # ponytail: one connection per call and a buffered cursor (whole result set is read
    # before truncation); add a pool / SSCursor if latency or huge results start to matter.
    try:
        with connect(database) as conn, conn.cursor() as cur:
            if read_only:
                cur.execute("START TRANSACTION READ ONLY")
            cur.execute(sql, args)  # args=None: pymysql leaves % in the SQL alone
            if cur.description is None:
                result = {"rows_affected": cur.rowcount, "last_insert_id": cur.lastrowid}
            else:
                rows = cur.fetchmany(MAX_ROWS + 1)
                result = {
                    "columns": [d[0] for d in cur.description],
                    "rows": rows[:MAX_ROWS],
                    "truncated": len(rows) > MAX_ROWS,
                }
            conn.rollback() if read_only else conn.commit()
            return result
    except pymysql.MySQLError as e:  # syntax errors, unknown tables, bad credentials: the caller needs the text
        raise QueryError(f"MySQL error {e}") from e


def check_read_only(sql: str) -> None:
    words = COMMENTS.sub(" ", sql).split()
    first = words[0].lower().rstrip("(") if words else ""
    if first not in READ_KEYWORDS:
        raise QueryError(f"Read-only: '{first or sql[:20]}' statements are not allowed. "
                         "Set MYSQL_ALLOW_WRITE=true to enable writes.")
    if FILE_WRITE.search(sql):
        raise QueryError("Read-only: INTO OUTFILE/DUMPFILE is not allowed.")


def ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def pick_db(database: str | None) -> str:
    db = database or os.getenv("MYSQL_DATABASE")
    if not db:
        raise QueryError("No database given: pass a database or set MYSQL_DATABASE.")
    return db


def compact(result: dict) -> str:
    # One compact JSON string. Indented JSON costs a model ~1.8x the tokens.
    return json.dumps(result, default=str, separators=(",", ":"), ensure_ascii=False)


# The four operations both front ends expose, each returning what the caller prints or sends.

def list_databases() -> str:
    return compact(run("SHOW DATABASES"))


def list_tables(database: str | None = None) -> str:
    return compact(run(
        "SELECT table_name, table_type, table_rows, table_comment FROM information_schema.tables "
        "WHERE table_schema = %s ORDER BY table_name",
        (pick_db(database),),
    ))


def describe_table(table: str, database: str | None = None) -> str:
    return run(f"SHOW CREATE TABLE {ident(pick_db(database))}.{ident(table)}")["rows"][0][1]


def query(sql: str, database: str | None = None) -> str:
    if not ALLOW_WRITE:
        check_read_only(sql)
    return compact(run(sql, database=database, read_only=not ALLOW_WRITE))
