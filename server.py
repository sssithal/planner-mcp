#!/usr/bin/env python3
"""
planner-mcp: a zero-dependency MCP server for the user's real macOS/iCloud calendar.

MCP over stdio is just JSON-RPC 2.0, one JSON object per line:
  client -> server on stdin, server -> client on stdout.
Logs MUST go to stderr (stdout is the protocol channel).

Lifecycle:
  1. client sends `initialize`       -> we reply with our capabilities
  2. client sends `notifications/initialized` (no reply; it's a notification)
  3. client sends `tools/list`       -> we reply with tool names + JSON schemas
  4. client sends `tools/call`       -> we run the tool, reply with text content
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

# Where add_event puts events when no calendar is named.
DEFAULT_CALENDAR = os.environ.get("PLANNER_DEFAULT_CALENDAR", "Calendar")


def log(*args):
    print("[planner-mcp]", *args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- calendar access
# Talks to Calendar.app via AppleScript (osascript ships with macOS). Any account
# added to Calendar.app (iCloud, Google, Exchange) shows up here too.
# User input is passed as argv, never spliced into the script, so no injection.

OSA_HELPERS = """
on isoDate(s) -- "YYYY-MM-DDTHH:MM" -> AppleScript date (locale-independent)
    set d to current date
    set day of d to 1
    set year of d to (text 1 thru 4 of s) as integer
    set month of d to (text 6 thru 7 of s) as integer
    set day of d to (text 9 thru 10 of s) as integer
    set hours of d to (text 12 thru 13 of s) as integer
    set minutes of d to (text 15 thru 16 of s) as integer
    set seconds of d to 0
    return d
end isoDate

on pad(n)
    return text -2 thru -1 of ("0" & n)
end pad

on fmt(d)
    return (year of d as text) & "-" & my pad(month of d as integer) & "-" & my pad(day of d) & "T" & my pad(hours of d) & ":" & my pad(minutes of d)
end fmt

on orEmpty(v) -- text with tabs/newlines flattened, since output is tab/line delimited
    if v is missing value then return ""
    set t to v as text
    set saved to AppleScript's text item delimiters
    repeat with ch in {tab, linefeed, return}
        set AppleScript's text item delimiters to ch
        set parts to text items of t
        set AppleScript's text item delimiters to " "
        set t to parts as text
    end repeat
    set AppleScript's text item delimiters to saved
    return t
end orEmpty
"""

OSA_LIST_CALENDARS = """
on run argv
    set out to ""
    tell application "Calendar"
        repeat with c in calendars
            set out to out & (name of c) & tab & (writable of c as text) & linefeed
        end repeat
    end tell
    return out
end run
"""

OSA_LIST_EVENTS = OSA_HELPERS + """
on run argv
    set startD to my isoDate(item 1 of argv)
    set endD to my isoDate(item 2 of argv)
    set out to ""
    tell application "Calendar"
        repeat with c in calendars
            set cname to name of c
            -- Bulk-fetch each property in one Apple Event; per-event access is very slow.
            set evs to a reference to (every event of c whose start date >= startD and start date < endD)
            set {ts, ss, es, ads, locs, ids} to {summary, start date, end date, allday event, location, uid} of evs
            repeat with i from 1 to count of ts
                set out to out & cname & tab & my orEmpty(item i of ts) & tab & my fmt(item i of ss) & tab & my fmt(item i of es) & tab & (item i of ads as text) & tab & my orEmpty(item i of locs) & tab & (item i of ids) & linefeed
            end repeat
        end repeat
    end tell
    return out
end run
"""

OSA_ADD_EVENT = OSA_HELPERS + """
on run argv
    set {t, s, e, calName, loc, nts, remind} to argv
    set startD to my isoDate(s)
    set endD to my isoDate(e)
    tell application "Calendar"
        if calName is "" then
            set cal to first calendar whose writable is true
        else
            set cal to first calendar whose name is calName
        end if
        tell cal
            set ev to make new event at end with properties {summary:t, start date:startD, end date:endD}
            if loc is not "" then set location of ev to loc
            if nts is not "" then set description of ev to nts
            if remind is not "" then
                tell ev to make new display alarm at end of display alarms with properties {trigger interval:(0 - (remind as integer))}
            end if
        end tell
        return (name of cal) & tab & (uid of ev)
    end tell
end run
"""


def _osa(script, *args):
    r = subprocess.run(["osascript", "-e", script, *args],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        msg = r.stderr.strip()
        if "-1743" in msg or "Not authorized" in msg:
            msg += (" | macOS blocked access to Calendar. Allow it in System Settings >"
                    " Privacy & Security > Automation (Calendar) for Claude.")
        raise RuntimeError(msg)
    return [line.split("\t") for line in r.stdout.splitlines() if line.strip()]


def _minute(s):
    """Normalize a date or datetime string to YYYY-MM-DDTHH:MM for AppleScript."""
    return datetime.fromisoformat(s).strftime("%Y-%m-%dT%H:%M")


# Reads go through calhelper (EventKit, ~0.1s) instead of AppleScript (minutes on big
# calendars). It is bundled as a tiny background .app so macOS asks the user for calendar
# access on its own behalf; a plain child process of Claude would be silently denied.
HERE = os.path.dirname(os.path.abspath(__file__))
HELPER_APP = os.path.join(HERE, ".build", "CalHelper.app")
HELPER_BIN = os.path.join(HELPER_APP, "Contents", "MacOS", "calhelper")
HELPER_SOURCES = [os.path.join(HERE, "calhelper.swift"), os.path.join(HERE, "calhelper-Info.plist")]
SDK_DIR = "/Library/Developer/CommandLineTools/SDKs"


def _build_calhelper():
    os.makedirs(os.path.dirname(HELPER_BIN), exist_ok=True)
    with open(HELPER_SOURCES[1], "rb") as src, \
            open(os.path.join(HELPER_APP, "Contents", "Info.plist"), "wb") as dst:
        dst.write(src.read())
    # The newest SDK can be ahead of the installed compiler, so fall back to older ones.
    sdks = sorted((f for f in os.listdir(SDK_DIR) if f[len("MacOSX"):-4].replace(".", "").isdigit()),
                  key=lambda f: [int(x) for x in f[len("MacOSX"):-4].split(".")], reverse=True) \
        if os.path.isdir(SDK_DIR) else []
    errors = []
    for sdk_args in [[]] + [["-sdk", os.path.join(SDK_DIR, f)] for f in sdks]:
        r = subprocess.run(["swiftc", "-O", *sdk_args, HELPER_SOURCES[0], "-o", HELPER_BIN],
                           capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            subprocess.run(["codesign", "--force", "-s", "-", "--identifier",
                            "local.planner-mcp.calhelper", HELPER_APP],
                           capture_output=True, check=True)
            log("built calhelper", *sdk_args)
            return
        errors.append(r.stderr.strip().splitlines()[-1:] or ["?"])
    raise RuntimeError(f"could not compile calhelper: {errors}")


def _calhelper(*args):
    newest_src = max(os.path.getmtime(f) for f in HELPER_SOURCES)
    if not os.path.exists(HELPER_BIN) or os.path.getmtime(HELPER_BIN) < newest_src:
        _build_calhelper()
    with tempfile.TemporaryDirectory() as tmp:
        out, err = os.path.join(tmp, "out"), os.path.join(tmp, "err")
        # `open` launches via LaunchServices so the helper is its own app in macOS's eyes.
        # No check=True: if the helper exits before `open -W` starts waiting, `open` fails
        # with a kevent error even though the output is complete. Judge by the output instead.
        subprocess.run(["open", "-W", "-n", "-g", "--stdout", out, "--stderr", err,
                        HELPER_APP, "--args", *args], capture_output=True, timeout=60)
        message = open(err).read().strip() if os.path.exists(err) else ""
        text = open(out).read() if os.path.exists(out) else ""
        if not text.strip():
            raise RuntimeError(message or "calhelper returned nothing")
        return json.loads(text)


def list_calendars():
    try:
        return _calhelper("calendars")
    except Exception as e:
        log("calhelper failed, falling back to AppleScript:", e)
        return [{"name": n, "writable": w == "true"} for n, w in _osa(OSA_LIST_CALENDARS)]


def list_events(start_date=None, end_date=None):
    start_date = start_date or date.today().isoformat()
    end_date = end_date or (date.fromisoformat(start_date) + timedelta(days=7)).isoformat()
    # end_date is inclusive for callers; both backends treat the end as exclusive.
    end_excl = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()
    try:
        return _calhelper("events", _minute(start_date), _minute(end_excl))
    except Exception as e:
        log("calhelper failed, falling back to AppleScript:", e)
    events = [
        {"calendar": c, "title": t, "start": s, "end": e, "all_day": a == "true", "location": loc,
         "id": uid}
        for c, t, s, e, a, loc, uid in _osa(OSA_LIST_EVENTS, _minute(start_date), _minute(end_excl))
    ]
    return sorted(events, key=lambda ev: ev["start"])


def add_event(title, start, end=None, calendar=None, location=None, notes=None,
                    reminder_minutes=None):
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end) if end else start_dt + timedelta(hours=1)
    if end_dt <= start_dt:
        raise ValueError("end must be after start")
    [[cal, uid]] = _osa(OSA_ADD_EVENT, title, _minute(start_dt.isoformat()),
                        _minute(end_dt.isoformat()), calendar or DEFAULT_CALENDAR, location or "", notes or "",
                        "" if reminder_minutes is None else str(int(reminder_minutes)))
    return {"created_in_calendar": cal, "uid": uid, "start": start_dt.isoformat(),
            "end": end_dt.isoformat()}


def update_event(id, title=None, start=None, end=None, location=None, notes=None):
    changes = {"title": title, "location": location, "notes": notes,
               "start": start and _minute(start), "end": end and _minute(end)}
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes:
        raise ValueError("nothing to change")
    return _calhelper("update", id, json.dumps(changes))


def delete_event(id):
    return _calhelper("delete", id)


def agenda(day=None):
    day = day or date.today().isoformat()
    return {"date": day, "events": list_events(day, day)}


DATE = {"type": "string", "description": "Date as YYYY-MM-DD"}

# Each tool: a Python function + a JSON Schema the model uses to call it.
# Descriptions matter: they're the model's only documentation.
TOOLS = {
    "agenda": (agenda, "Everything on the user's calendar for one day (default today). "
                       "Great for 'what's on my plate today?'.", {"day": DATE}, []),
    "list_events": (list_events,
        "List events from the user's calendars between two dates (inclusive; default next "
        "7 days). Repeating events appear as individual occurrences.",
        {"start_date": DATE, "end_date": DATE}, []),
    "add_event": (add_event,
        "Create an event on the user's calendar. Also use this for reminders: the user keeps "
        "reminders as calendar events with an alert (set reminder_minutes).", {
        "title": {"type": "string"},
        "start": {"type": "string", "description": "ISO datetime, e.g. 2026-10-01T10:40"},
        "end": {"type": "string", "description": "ISO datetime; defaults to start + 1 hour"},
        "calendar": {"type": "string", "description": f"Calendar name; defaults to \"{DEFAULT_CALENDAR}\", "
                     "the user's main calendar. Only pass this if the user names a different one."},
        "location": {"type": "string"}, "notes": {"type": "string"},
        "reminder_minutes": {"type": "integer",
                             "description": "Alert this many minutes before the start"},
    }, ["title", "start"]),
    "update_event": (update_event,
        "Change one event: pass its id (from list_events, agenda or add_event's uid) and only the "
        "fields to change. Moving the start keeps the event's length unless end is also given. "
        "Repeating events and read-only calendars are refused.", {
        "id": {"type": "string", "description": "The event's id"},
        "title": {"type": "string"},
        "start": {"type": "string", "description": "New ISO datetime, e.g. 2026-10-01T10:40"},
        "end": {"type": "string", "description": "New ISO datetime"},
        "location": {"type": "string"}, "notes": {"type": "string"},
    }, ["id"]),
    "delete_event": (delete_event,
        "Delete one event by id. Only call this after the user has confirmed which event to "
        "delete. Repeating events and read-only calendars are refused.",
        {"id": {"type": "string", "description": "The event's id"}}, ["id"]),
    "list_calendars": (list_calendars,
        "List the user's calendars (iCloud, Google, etc.) and whether each is writable.", {}, []),
}


# --------------------------------------------------------------------------- protocol

def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}

    if method == "initialize":
        return {
            # Echo the client's version; this server only uses long-stable features.
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "planner-mcp", "version": "0.3.0"},
            "instructions": f"Today's date is {date.today().isoformat()}. "
                            "These tools read and write the user's real calendars (iCloud, synced "
                            "with their iPhone). The user keeps reminders as calendar events with "
                            "alerts, so for 'remind me to X' create an event with reminder_minutes.",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [
            {"name": name, "description": desc,
             "inputSchema": {"type": "object", "properties": props, "required": req}}
            for name, (_, desc, props, req) in TOOLS.items()
        ]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            raise LookupError(f"unknown tool {name}")
        try:
            result = TOOLS[name][0](**args)
            return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
        except Exception as e:
            # Tool errors go back to the model as content so it can recover.
            return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
    raise LookupError(f"method not found: {method}")


def main():
    log("started, default calendar =", DEFAULT_CALENDAR)
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:  # notification: never reply
            continue
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32601, "message": str(e)}}
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"],
                     "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
