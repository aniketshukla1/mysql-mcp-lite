"""The `mysql-mcp-lite` command. No arguments runs the MCP server; the subcommands do the same work from a shell."""

import argparse
import sys

from . import db
from .server import serve


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mysql-mcp-lite",
        description="MySQL for AI agents. With no command it runs the MCP server on stdio; the commands below "
        "do the same work from a shell. Connection settings come from the MYSQL_* environment variables.",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.add_parser("serve", help="run the MCP server on stdio (the default)")
    sub.add_parser("databases", help="list databases (JSON)")
    tables = sub.add_parser("tables", help="list tables and views with approximate row counts (JSON)")
    describe = sub.add_parser("describe", help="print a table's CREATE TABLE statement")
    describe.add_argument("table")
    query = sub.add_parser("query", help="run one SQL statement (JSON); reads the SQL from stdin when omitted")
    query.add_argument("sql", nargs="?", default="-", help="the statement, or - to read stdin (the default)")
    for p in (tables, describe, query):
        p.add_argument("-d", "--database", help="database to use (default: MYSQL_DATABASE)")
    args = parser.parse_args(argv)

    if args.command in (None, "serve"):
        serve()
        return
    try:
        if args.command == "databases":
            out = db.list_databases()
        elif args.command == "tables":
            out = db.list_tables(args.database)
        elif args.command == "describe":
            out = db.describe_table(args.table, args.database)
        else:
            out = db.query(sys.stdin.read() if args.sql == "-" else args.sql, args.database)
    except db.QueryError as e:
        sys.exit(f"error: {e}")
    print(out)
