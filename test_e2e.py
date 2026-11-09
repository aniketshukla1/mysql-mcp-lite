# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=2.3,<3", "pymysql>=1.1"]
# ///
"""End-to-end check: real MySQL <- mysql_mcp_lite.py (spawned over stdio, as any MCP client would) <- MCP client.

Defaults target a throwaway container:
  docker run -d --name mysql-mcp-test -e MYSQL_ROOT_PASSWORD=testroot -p 127.0.0.1:33306:3306 mysql:8.4
Run: uv run --script test_e2e.py   (override with MYSQL_HOST/PORT/USER/PASSWORD)
"""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pymysql
from mcp import Client, StdioServerParameters

DB = {
    "MYSQL_HOST": os.getenv("MYSQL_HOST", "127.0.0.1"),
    "MYSQL_PORT": os.getenv("MYSQL_PORT", "33306"),
    "MYSQL_USER": os.getenv("MYSQL_USER", "root"),
    "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD", "testroot"),
}
SERVER = str(Path(__file__).with_name("mysql_mcp_lite.py"))


def seed() -> None:
    conn = pymysql.connect(host=DB["MYSQL_HOST"], port=int(DB["MYSQL_PORT"]), user=DB["MYSQL_USER"],
                           password=DB["MYSQL_PASSWORD"], autocommit=True)
    with conn, conn.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS mcp_e2e")
        cur.execute("DROP TABLE IF EXISTS mcp_e2e.orders")
        cur.execute("CREATE TABLE mcp_e2e.orders (id INT AUTO_INCREMENT PRIMARY KEY, item VARCHAR(50) NOT NULL,"
                    " price DECIMAL(8,2), created DATETIME DEFAULT CURRENT_TIMESTAMP)")
        cur.execute("INSERT INTO mcp_e2e.orders (item, price) VALUES ('apple', 1.50), ('banana', 0.25), ('cherry', 4.00)")


def server(**extra: str) -> StdioServerParameters:
    # Inherit the shell env minus any MYSQL_* so the test fully controls the server's config.
    env = {k: v for k, v in os.environ.items() if not k.startswith("MYSQL_")}
    env |= DB | {"MYSQL_DATABASE": "mcp_e2e"} | extra
    return StdioServerParameters(command=shutil.which("uv"), args=["run", "--quiet", "--script", SERVER], env=env)


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

    async with Client(server(MYSQL_ALLOW_WRITE="true", MYSQL_MAX_ROWS="2")) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
        assert tools["query"].annotations.destructive_hint is True
        r = await ok(c, "query", sql="INSERT INTO orders (item, price) VALUES ('date', 3.10)")
        assert r == {"rows_affected": 1, "last_insert_id": 4}, r
        r = await ok(c, "query", sql="SELECT item FROM orders ORDER BY id")
        assert r["rows"] == [["apple"], ["banana"]] and r["truncated"] is True, r

    print("e2e OK: 4 tools + /mysql prompt over stdio, read-only guards (3 layers), writes, truncation")


if __name__ == "__main__":
    asyncio.run(main())
