"""End-to-end check against a real MySQL: the MCP server over stdio (as an agent runs it),
and the shell commands the skill runs.

Needs a throwaway MySQL:
  docker run -d --name mysql-mcp-test -e MYSQL_ROOT_PASSWORD=testroot -p 127.0.0.1:33306:3306 mysql:8.4
Run from the repo root: uv run tests/test_e2e.py   (override with MYSQL_HOST/PORT/USER/PASSWORD)
"""

import asyncio
import json
import os
import subprocess
import sys

import pymysql
from mcp import Client, StdioServerParameters

DB = {
    "MYSQL_HOST": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "MYSQL_PORT": os.getenv("MYSQL_PORT", "33306"),
    "MYSQL_USER": os.getenv("MYSQL_USER", "root"),
    "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD", "testroot"),
}


def seed() -> None:
    conn = pymysql.connect(host=DB["MYSQL_HOST"], port=int(DB["MYSQL_PORT"]), user=DB["MYSQL_USER"],
                           password=DB["MYSQL_PASSWORD"], autocommit=True)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS mcp_e2e")
        cur.execute("DROP TABLE IF EXISTS mcp_e2e.orders")
        cur.execute("CREATE TABLE mcp_e2e.orders (id INT AUTO_INCREMENT PRIMARY KEY, item VARCHAR(50) NOT NULL,"
                    " price DECIMAL(8,2), created DATETIME DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("INSERT INTO mcp_e2e.orders (item, price) VALUES ('apple', 1.50), ('banana', 0.25), ('cherry', 4.00)")


def env(**extra: str) -> dict:
    # Inherit the shell env minus any MYSQL_* so the test fully controls the config.
    base = {k: v for k, v in os.environ.items() if not k.startswith("MYSQL_")}
    return base | DB | {"MYSQL_DATABASE": "mcp_e2e"} | extra


def server(**extra: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "mysql_mcp_lite"], env=env(**extra))


def cli(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "mysql_mcp_lite", *args], input=stdin,
                          capture_output=True, text=True, env=env(), timeout=60)


async def ok(c: Client, tool: str, **args):
    r = await c.call_tool(tool, args)
    text = r.content[0].text
    assert not r.is_error, f"{tool}({args}) failed: {text}"
    assert len(r.content) == 1 and r.structured_content is None, f"{tool}: want one text block, no structured copy"
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text  # describe_table: plain DDL
    assert text == json.dumps(data, separators=(",", ":"), ensure_ascii=False), f"{tool}: not compact JSON: {text[:80]}"
    return data


async def fails(c: Client, tool: str, expect: str, **args) -> None:
    r = await c.call_tool(tool, args)
    text = r.content[0].text
    assert r.is_error and expect in text, f"{tool}({args}) should fail with {expect!r}, got: {text}"


async def main() -> None:
    seed()

    async with Client(server()) as c:  # default: read-only
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert set(tools) == {"list_databases", "list_tables", "describe_table", "query"}, set(tools)
        assert tools["query"].annotations.read_only_hint is True
        assert [p.name for p in (await c.list_prompts()).prompts] == ["mysql"]
        msg = (await c.get_prompt("mysql", {"question": "top 2 orders"})).messages[0].content.text
        assert msg.startswith("top 2 orders") and "describe_table" in msg, msg

        assert ["mcp_e2e"] in (await ok(c, "list_databases"))["rows"]
        assert (await ok(c, "list_tables"))["rows"][0][0] == "orders"
        ddl = await ok(c, "describe_table", table="orders")
        assert "CREATE TABLE `orders`" in ddl and "PRIMARY KEY" in ddl, ddl

        r = await ok(c, "query", sql="SELECT item, price, created FROM orders WHERE item LIKE 'b%'")
        assert r["columns"] == ["item", "price", "created"] and r["truncated"] is False, r
        assert r["rows"][0][:2] == ["banana", "0.25"] and isinstance(r["rows"][0][2], str), r  # Decimal/datetime -> str

        await fails(c, "query", "not allowed", sql="DELETE FROM orders")
        await fails(c, "query", "not allowed", sql="/* sneaky */ DROP TABLE orders")
        await fails(c, "query", "OUTFILE", sql="SELECT * FROM orders INTO OUTFILE '/tmp/x.csv'")
        # Passes the keyword check; the server-side READ ONLY transaction must stop it.
        await fails(c, "query", "READ ONLY",
                    sql="WITH x AS (SELECT id FROM orders) DELETE FROM orders WHERE id IN (SELECT id FROM x)")
        # DDL would escape the READ ONLY transaction (implicit commit): stacking must be impossible.
        await fails(c, "query", "1064", sql="SELECT 1; DROP TABLE orders")
        await fails(c, "describe_table", "doesn't exist", table="nope")
        assert (await ok(c, "query", sql="SELECT COUNT(*) FROM orders"))["rows"] == [[3]]

    # The skill's path: the same operations as shell commands, no MCP involved.
    r = cli("databases")
    assert r.returncode == 0 and ["mcp_e2e"] in json.loads(r.stdout)["rows"], r
    r = cli("tables")
    assert r.returncode == 0 and json.loads(r.stdout)["rows"][0][0] == "orders", r
    r = cli("describe", "orders")
    assert r.returncode == 0 and "CREATE TABLE `orders`" in r.stdout, r
    r = cli("query", stdin="SELECT item FROM orders WHERE item LIKE 'b%'")  # SQL on stdin, as from a heredoc
    assert r.returncode == 0 and json.loads(r.stdout)["rows"] == [["banana"]], r
    r = cli("query", "DELETE FROM orders")
    assert r.returncode == 1 and "not allowed" in r.stderr, r

    async with Client(server(MYSQL_ALLOW_WRITE="true", MYSQL_MAX_ROWS="2")) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["query"].annotations.destructive_hint is True
        r = await ok(c, "query", sql="INSERT INTO orders (item, price) VALUES ('date', 3.10)")
        assert r == {"rows_affected": 1, "last_insert_id": 4}, r
        r = await ok(c, "query", sql="SELECT item FROM orders ORDER BY id")
        assert r["rows"] == [["apple"], ["banana"]] and r["truncated"] is True, r

    print("e2e OK: MCP (4 tools, /mysql prompt, read-only guards, writes, truncation) + the skill's shell commands")


if __name__ == "__main__":
    asyncio.run(main())
