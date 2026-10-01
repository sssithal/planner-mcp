# planner-mcp

A zero-dependency [MCP](https://modelcontextprotocol.io) server that lets Claude read and add events on your macOS calendar. Any account in Calendar.app (iCloud, Google, Exchange) works.

## Tools

| Tool | What it does |
| --- | --- |
| `list_calendars` | Lists calendars and whether each is writable |
| `list_events` | Lists events between two dates |
| `agenda` | Lists one day's events (defaults to today) |
| `add_event` | Creates an event, optionally with a reminder alert |

## Requirements

- macOS with Python 3 (no packages needed)
- Xcode Command Line Tools (`xcode-select --install`), used to compile the Swift calendar helper

## Setup

Register the server with Claude Code:

```bash
claude mcp add planner -- python3 /path/to/planner-mcp/server.py
```

On first use, the server compiles `calhelper.swift` into `.build/CalHelper.app` and macOS asks for calendar access. Allow it. If the helper can't be built, reads fall back to AppleScript, which is slower.

### Options

- `PLANNER_DEFAULT_CALENDAR`: the calendar `add_event` uses when none is named (default `Calendar`)

## Testing

`test_client.py` acts as an MCP client over stdio. It only reads; it never creates or deletes events.

```bash
python3 test_client.py
```

## Example agents

[`examples/agents/`](examples/agents/) has three scheduled agents built on this server: a morning brief, an afternoon wrap-up, and a Friday weekly review. They only read your calendar on a schedule, and events get added only when you approve them in a live chat. The folder also covers how to give an agent safe, limited access to Gmail.
