# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.3,<3", "pymysql>=1.1"]
# ///
"""MySQL MCP server for any MCP client (stdio): list databases/tables, show table DDL, run SQL, /mysql prompt."""

import json
import os
import re

import pymysql
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # the only exception whose text reaches the model
from mcp.types import ToolAnnotations

ALLOW_WRITE = os.getenv("MYSQL_ALLOW_WRITE", "").lower() in ("1", "true", "yes")
MAX_ROWS = int(os.getenv("MYSQL_MAX_ROWS", "500"))
TIMEOUT_MS = int(os.getenv("MYSQL_TIMEOUT_MS", "30000"))
READ_ONLY = ToolAnnotations(readOnlyHint=True)

# Read-only mode has three layers. These checks reject the obvious; the server-side
# READ ONLY transaction blocks DML they miss; a SELECT-only MySQL user is the real boundary.
READ_KEYWORDS = {"select", "show", "describe", "desc", "explain", "with", "table", "values"}
COMMENTS = re.compile(r"/\*.*?\*/|(?:--\s|#)[^\n]*", re.S)
FILE_WRITE = re.compile(r"outfile|dumpfile", re.I)  # raw substring: comments can't split a keyword

mcp = MCPServer(
    "mysql",
    instructions="Explore a MySQL database: list_databases -> list_tables -> describe_table, then query. "
    + ("Writes are enabled." if ALLOW_WRITE else "Read-only: only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH run."),
)


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
    except pymysql.MySQLError as e:  # syntax errors, unknown tables, bad credentials: the model needs the text
        raise ToolError(f"MySQL error {e}") from e


def check_read_only(sql: str) -> None:
    words = COMMENTS.sub(" ", sql).split()
    first = words[0].lower().rstrip("(") if words else ""
    if first not in READ_KEYWORDS:
        raise ToolError(f"Read-only server: '{first or sql[:20]}' statements are not allowed. "
                        "Start the server with MYSQL_ALLOW_WRITE=true to enable writes.")
    if FILE_WRITE.search(sql):
        raise ToolError("Read-only server: INTO OUTFILE/DUMPFILE is not allowed.")


def ident(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def pick_db(database: str | None) -> str:
    db = database or os.getenv("MYSQL_DATABASE")
    if not db:
        raise ToolError("No database given: pass `database` or set MYSQL_DATABASE.")
    return db


def compact(result: dict) -> str:
    # One compact JSON text block. The SDK's default for a dict is indent=2: ~1.8x the tokens.
    return json.dumps(result, default=str, separators=(",", ":"), ensure_ascii=False)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def list_databases() -> str:
    """List the databases visible to the configured MySQL user."""
    return compact(run("SHOW DATABASES"))


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def list_tables(database: str | None = None) -> str:
    """List tables and views in a database (default: MYSQL_DATABASE), with approximate row counts."""
    return compact(run(
        "SELECT table_name, table_type, table_rows, table_comment FROM information_schema.tables "
        "WHERE table_schema = %s ORDER BY table_name",
        (pick_db(database),),
    ))


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def describe_table(table: str, database: str | None = None) -> str:
    """Return the CREATE TABLE statement: columns, types, indexes and foreign keys."""
    return run(f"SHOW CREATE TABLE {ident(pick_db(database))}.{ident(table)}")["rows"][0][1]


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=not ALLOW_WRITE, destructiveHint=ALLOW_WRITE),
          structured_output=False)
def query(sql: str, database: str | None = None) -> str:
    """Run one SQL statement. Returns JSON {columns, rows, truncated}: at most MYSQL_MAX_ROWS rows,
    `truncated` true when cut; writes return {rows_affected, last_insert_id}.
    Read-only (SELECT/SHOW/DESCRIBE/EXPLAIN/WITH) unless the server runs with MYSQL_ALLOW_WRITE=true."""
    if not ALLOW_WRITE:
        check_read_only(sql)
    return compact(run(sql, database=database, read_only=not ALLOW_WRITE))


@mcp.prompt(name="mysql", description="Ask about the database in plain words; the agent explores the schema and runs SQL.")
def mysql_prompt(question: str = "") -> str:
    ask = question.strip() or "Give me an overview of this database: its tables and what each one holds."
    return (
        f"{ask}\n\n"
        "Answer with this server's MySQL tools: list_tables (list_databases if the database is unclear), "
        "describe_table for every table you will query (never guess column names), then the smallest query "
        "that answers it (only the columns needed, LIMIT when listing rows). Reply in plain words first, "
        "then show the SQL you ran, and say so if a result came back truncated."
    )


def main() -> None:  # the `mysql-mcp-lite` command
    mcp.run()


if __name__ == "__main__":
    main()
