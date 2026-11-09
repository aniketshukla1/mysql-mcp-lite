# mysql-mcp-lite

MySQL for any AI agent: one MCP server, one install command. Works in Claude Code, Codex, Cursor, OpenCode, Gemini CLI, VS Code, Claude Desktop and any other MCP client.

| Tool | Does |
|---|---|
| `list_databases` | Databases visible to the configured user |
| `list_tables(database?)` | Tables and views, with approximate row counts |
| `describe_table(table, database?)` | `SHOW CREATE TABLE`: columns, indexes, foreign keys |
| `query(sql, database?)` | One statement → compact JSON `{columns, rows, truncated}` |

Plus a `/mysql` prompt: ask in plain words, and the agent reads the schema and writes the SQL.

Read-only by default. Needs [uv](https://docs.astral.sh/uv/) and MySQL 5.7.8+ / 8.x.

## Install

Every agent runs the same command:

```bash
uvx --from git+https://github.com/aniketshukla1/mysql-mcp-lite mysql-mcp-lite
```

From a local clone, give the folder instead: `uvx --from /path/to/mysql-mcp-lite mysql-mcp-lite`.

The first start downloads dependencies (about 10 seconds). If an agent shows the server as disconnected on its first launch, restart the agent.

The examples use a read-only user `mcp_ro` on database `shop` (see [Safety](#safety)). Swap in your values.

### Claude Code

```bash
claude mcp add -s user mysql -e MYSQL_HOST=127.0.0.1 -e MYSQL_USER=mcp_ro -e MYSQL_PASSWORD=change-me -e MYSQL_DATABASE=shop -- uvx --from git+https://github.com/aniketshukla1/mysql-mcp-lite mysql-mcp-lite
```

### Codex

```bash
codex mcp add mysql --env MYSQL_HOST=127.0.0.1 --env MYSQL_USER=mcp_ro --env MYSQL_PASSWORD=change-me --env MYSQL_DATABASE=shop -- uvx --from git+https://github.com/aniketshukla1/mysql-mcp-lite mysql-mcp-lite
```

### Cursor, Gemini CLI, Claude Desktop and most other clients

They share one JSON shape:

```json
{
  "mcpServers": {
    "mysql": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aniketshukla1/mysql-mcp-lite", "mysql-mcp-lite"],
      "env": {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_USER": "mcp_ro",
        "MYSQL_PASSWORD": "change-me",
        "MYSQL_DATABASE": "shop"
      }
    }
  }
}
```

| Client | File | Note |
|---|---|---|
| Cursor | `~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` | `cursor-agent`: approve once with `cursor-agent mcp enable mysql` |
| Gemini CLI | `~/.gemini/settings.json` | Gemini turns MCP servers off in folders you haven't trusted |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` | If it can't find `uvx`, put the full path from `which uvx` in `command` |

### OpenCode

`opencode.json` in the project, or `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "mysql": {
      "type": "local",
      "command": ["uvx", "--from", "git+https://github.com/aniketshukla1/mysql-mcp-lite", "mysql-mcp-lite"],
      "environment": {
        "MYSQL_HOST": "127.0.0.1",
        "MYSQL_USER": "mcp_ro",
        "MYSQL_PASSWORD": "change-me",
        "MYSQL_DATABASE": "shop"
      }
    }
  }
}
```

### VS Code

`.vscode/mcp.json`, the same fields under `servers`:

```json
{
  "servers": {
    "mysql": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/aniketshukla1/mysql-mcp-lite", "mysql-mcp-lite"],
      "env": { "MYSQL_HOST": "127.0.0.1", "MYSQL_USER": "mcp_ro", "MYSQL_PASSWORD": "change-me", "MYSQL_DATABASE": "shop" }
    }
  }
}
```

Checked: Claude Code, cursor-agent, Gemini CLI and OpenCode connected to the server from these configs, and Codex accepted its config. VS Code and Claude Desktop use their standard formats but weren't run.

## The /mysql prompt

Clients that support MCP prompts show it as a command: `/mcp__mysql__mysql` in Claude Code, `/mcp.mysql.mysql` in VS Code, `/mysql` in Gemini CLI. Give it a question, such as `top 5 customers by revenue last month`; with no question it summarizes the database.

## Config

| Env var | Default | Meaning |
|---|---|---|
| `MYSQL_HOST` | `127.0.0.1` | |
| `MYSQL_PORT` | `3306` | |
| `MYSQL_USER` | `root` | |
| `MYSQL_PASSWORD` | empty | |
| `MYSQL_DATABASE` | none | Default database for `list_tables`, `describe_table`, `query` |
| `MYSQL_ALLOW_WRITE` | off | `true` lets `query` run any statement, committed |
| `MYSQL_MAX_ROWS` | `500` | Rows returned per call; `truncated: true` when cut |
| `MYSQL_TIMEOUT_MS` | `30000` | Per-SELECT limit (`MAX_EXECUTION_TIME`) |

## Safety

Read-only mode stacks three layers:

1. The statement must start with `SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `WITH`, `TABLE` or `VALUES`; `INTO OUTFILE/DUMPFILE` is rejected. One statement per call (`SELECT 1; DROP …` fails in MySQL).
2. Every call runs inside `START TRANSACTION READ ONLY` and is rolled back, so DML that slips past layer 1 (e.g. `WITH … DELETE`) fails in MySQL.
3. **The real boundary: connect as a SELECT-only user.**

```sql
CREATE USER 'mcp_ro'@'%' IDENTIFIED BY 'change-me';
GRANT SELECT, SHOW VIEW ON shop.* TO 'mcp_ro'@'%';
```

## Develop

```bash
docker run -d --name mysql-mcp-test -e MYSQL_ROOT_PASSWORD=testroot -p 127.0.0.1:33306:3306 mysql:8.4
```

```bash
uv run --script test_e2e.py
```

Starts `mysql_mcp_lite.py` over stdio the way an agent does, then checks the 4 tools, the `/mysql` prompt, each read-only layer, write mode and truncation against a real MySQL.
