"""MCP server: the four database operations as tools, plus the /mysql prompt."""

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import db

READ_ONLY = ToolAnnotations(readOnlyHint=True)

mcp = MCPServer(
    "mysql",
    instructions="Explore a MySQL database: list_databases -> list_tables -> describe_table, then query. "
    + ("Writes are enabled." if db.ALLOW_WRITE else "Read-only: only SELECT/SHOW/DESCRIBE/EXPLAIN/WITH run."),
)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def list_databases() -> str:
    """List the databases visible to the configured MySQL user."""
    return db.list_databases()


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def list_tables(database: str | None = None) -> str:
    """List tables and views in a database (default: MYSQL_DATABASE), with approximate row counts."""
    return db.list_tables(database)


@mcp.tool(annotations=READ_ONLY, structured_output=False)
def describe_table(table: str, database: str | None = None) -> str:
    """Return the CREATE TABLE statement: columns, types, indexes and foreign keys."""
    return db.describe_table(table, database)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=not db.ALLOW_WRITE, destructiveHint=db.ALLOW_WRITE),
          structured_output=False)
def query(sql: str, database: str | None = None) -> str:
    """Run one SQL statement. Returns JSON {columns, rows, truncated}: at most MYSQL_MAX_ROWS rows,
    `truncated` true when cut; writes return {rows_affected, last_insert_id}.
    Read-only (SELECT/SHOW/DESCRIBE/EXPLAIN/WITH) unless the server runs with MYSQL_ALLOW_WRITE=true."""
    return db.query(sql, database)


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


def serve() -> None:
    mcp.run()
