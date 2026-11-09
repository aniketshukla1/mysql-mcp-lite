---
name: mysql
description: Query and explore the user's MySQL database from the shell with the mysql-mcp-lite command, read-only by default, with no MCP server needed. Use this whenever an answer needs data or schema from a MySQL database (counts, lookups, reports, "which customers...", table structure, checking a query against real data), even if the user doesn't say "MySQL" or "SQL", as long as their data lives in MySQL.
license: MIT
compatibility: Needs a shell, uv (or mysql-mcp-lite on PATH), and MYSQL_* environment variables pointing at a reachable MySQL 5.7.8+ or 8.x server.
---

# MySQL

Answer questions about the user's MySQL database by running the `mysql-mcp-lite` command in your shell. It prints compact JSON, so results stay cheap to read.

Run it as `mysql-mcp-lite <command>` if it's on PATH (installed with `uv tool install git+https://github.com/aniketshukla1/mysql-mcp-lite`). Otherwise put `uvx --from git+https://github.com/aniketshukla1/mysql-mcp-lite` in front of it, for example `uvx --from git+https://github.com/aniketshukla1/mysql-mcp-lite mysql-mcp-lite tables`. The first uvx run downloads dependencies and takes about 10 seconds.

| Command | Prints |
|---|---|
| `databases` | JSON: the databases the user can see |
| `tables [-d DB]` | JSON: tables and views with approximate row counts |
| `describe TABLE [-d DB]` | The table's CREATE TABLE statement: columns, indexes, foreign keys |
| `query [-d DB]` | JSON `{columns, rows, truncated}` for the SQL read from stdin |

Send SQL through a quoted heredoc. Inside double quotes the shell would expand backticks and `$`, which MySQL identifiers and strings often contain:

```bash
mysql-mcp-lite query <<'SQL'
SELECT name, total FROM orders ORDER BY total DESC LIMIT 5
SQL
```

## How to answer

1. Run `tables` to see what exists, and `databases` first if you don't know which database to use.
2. Run `describe` on each table you'll query. Column names are rarely what they seem, and a guessed one costs a failed query.
3. Run the smallest `query` that answers the question: only the columns you need, with `LIMIT` when listing rows. Results are capped at 500 rows by default, and `"truncated": true` means you saw only part of them.
4. Answer in plain words first, then show the SQL you ran, so the user can check it or reuse it.

## Settings and errors

The command reads `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` and `MYSQL_DATABASE` from the environment. If it can't connect, tell the user which variables to set and where (their shell profile or the agent's environment settings). Don't ask them to paste a password into the chat: conversations are stored, and a password there can leak.

Writes are off unless the user started with `MYSQL_ALLOW_WRITE=true`, so a write fails with "not allowed". Tell the user instead of looking for a way around it: read-only is their decision.
