#!/usr/bin/env python3
"""Pretend to be an MCP client (like Claude) and talk to server.py over stdio.

Read-only against the real calendar: it lists things and checks error handling,
but never creates or deletes events.
"""
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
proc = subprocess.Popen([sys.executable, os.path.join(here, "server.py")],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
next_id = 0


def send(method, params=None, notify=False):
    global next_id
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        next_id += 1
        msg["id"] = next_id
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    if not notify:
        return json.loads(proc.stdout.readline())


def call(tool, **args):
    res = send("tools/call", {"name": tool, "arguments": args})["result"]
    text = res["content"][0]["text"]
    print(f"\n> {tool}({args}) -> {text[:200]}{'...' if len(text) > 200 else ''}")
    return res


print(send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "test", "version": "0"}})["result"]["serverInfo"])
send("notifications/initialized", notify=True)
print("tools:", [t["name"] for t in send("tools/list")["result"]["tools"]])

assert not call("list_calendars").get("isError")
assert not call("agenda").get("isError")
assert not call("list_events", start_date="2026-01-01", end_date="2026-01-07").get("isError")
# Bad input must come back as a tool error, before anything touches the calendar.
assert call("add_event", title="bad", start="tomorrow-ish").get("isError")
assert call("add_event", title="bad", start="2026-10-01T10:00", end="2026-10-01T09:00").get("isError")
assert "error" in send("nope/method")
proc.stdin.close()
proc.wait()
print("\nall good ✅")
