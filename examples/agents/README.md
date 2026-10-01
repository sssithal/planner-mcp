# Example agents

Three scheduled agents built on planner-mcp. They frame a workday: a brief in the morning, a wrap-up in the afternoon, and a review on Friday. They read your calendar on a schedule, and events only get added when you say yes in a live chat.

Open [`overview.html`](overview.html) in a browser for the week-at-a-glance chart and the architecture diagram.

| Agent | Schedule (cron) | Reads | Produces |
| --- | --- | --- | --- |
| [Morning brief](morning-brief.md) | `30 7 * * 1-5` (weekdays 7:30) | Calendar, starred-email subjects | A one-page brief |
| [Afternoon wrap-up](afternoon-wrap-up.md) | `45 15 * * 1-4` (Mon–Thu 3:45) | Calendar, starred-email subjects | A chat message |
| [Weekly review](weekly-review.md) | `0 15 * * 5` (Fri 3:00) | Calendar only | A private Notion page |

On Fridays the weekly review replaces the wrap-up, so you get one end-of-day message instead of two.

## How it fits together

```
 Scheduler (Claude app, cron)
        │ fires
        ▼
 ┌─ scheduled runs (read-only) ─┐        brief page / chat message
 │  morning brief               │ ─────────────────────────────────►  You
 │  afternoon wrap-up           │                                      │
 │  weekly review ──────────────┼──► Notion page ──── link ───────────►│
 └──────────────────────────────┘                                      │
        ▲ list_events / agenda          ▲ is:starred, subjects only    │ reply "yes"
        │                               │                              ▼
   planner-mcp ◄──── add_event (approved only) ──────────────── live chat session
   (Calendar.app)                  Gmail
```

The scheduled runs never call `add_event`, `update_event` or `delete_event`. The only path that writes to your calendar is a live chat where you approve each block.

## Setup

1. Register planner-mcp (see the [main README](../../README.md)).
2. In the Claude desktop app, create a scheduled task for each agent. Paste the prompt from its file, fill in the `<placeholders>`, and use the cron expression from the table above.
3. Click **Run now** once on each task and approve only the tools it needs (see below). Approvals are saved on the task and reused by every later run.

Scheduled tasks run while the app is open. If it's closed when a task is due, the task runs the next time you open it.

## Email: starred messages, subject lines only

Reading an inbox on a schedule is the riskiest part of this setup. Anyone can email you, and an unattended agent that reads message bodies can be targeted with hidden instructions (prompt injection). These examples limit email access in three layers.

1. **You choose what the agent sees.** Agents search only `is:starred`. A sender can't star their own email, so nothing reaches the agent unless you've already looked at it.
2. **Agents never open messages.** They use only the sender, subject and date from search results, and never call `get_thread` or `get_message`. Bodies are where injected text, one-time codes, reset links and attachments live, and they stay in your inbox.
3. **Tool approvals are the hard limit.** When you click **Run now**, allow only the Gmail search tool. Deny send, forward, reply, draft, label and trash. Instructions in a prompt are guidance; a denied tool can't run.

Starring doubles as a reminder system: star an email to see it in your brief and wrap-up, and unstar it when it's handled. The weekly review doesn't read email at all.

The trade-off is that an agent won't spot an important email you forgot to star.

## Approvals per agent

| Agent | Allow | Deny |
| --- | --- | --- |
| Morning brief | planner `list_events`, `agenda`; Gmail search | planner writes; all other Gmail tools |
| Afternoon wrap-up | planner `list_events`, `agenda`; Gmail search | planner writes; all other Gmail tools |
| Weekly review | planner `list_events`; Notion create page | planner writes; all Gmail tools |
