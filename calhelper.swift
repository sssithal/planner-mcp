// calhelper: fast access to macOS calendars via EventKit.
// server.py compiles this on first use (swiftc ships with Xcode Command Line Tools).
//
//   calhelper calendars
//   calhelper events 2026-10-01T00:00 2026-10-08T00:00
//   calhelper update <id> '{"title": "...", "start": "2026-10-01T10:00"}'
//   calhelper delete <id>
//
// Prints JSON to stdout. Recurring events are expanded into individual occurrences.
// <id> is the event's iCalendar UID, the same "uid" that add_event returns.
import EventKit
import Foundation

func fail(_ msg: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

func printJSON(_ obj: Any) {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    print(String(data: data, encoding: .utf8)!)
}

let store = EKEventStore()
let done = DispatchSemaphore(value: 0)
var granted = false
if #available(macOS 14.0, *) {
    store.requestFullAccessToEvents { ok, _ in granted = ok; done.signal() }
} else {
    store.requestAccess(to: .event) { ok, _ in granted = ok; done.signal() }
}
done.wait()
if !granted {
    fail("Calendar access denied. Allow it in System Settings > Privacy & Security > Calendars.", code: 2)
}

let fmt = DateFormatter()
fmt.locale = Locale(identifier: "en_US_POSIX")
fmt.dateFormat = "yyyy-MM-dd'T'HH:mm"

func describe(_ e: EKEvent) -> [String: Any] {
    ["id": e.calendarItemExternalIdentifier ?? "", "title": e.title ?? "", "calendar": e.calendar.title,
     "start": fmt.string(from: e.startDate), "end": fmt.string(from: e.endDate), "location": e.location ?? ""]
}

// Edits are limited to single, non-repeating events on writable calendars, so a wrong
// id can never change a whole series or someone else's shared event.
func findEvent(_ id: String) -> EKEvent {
    let matches = store.calendarItems(withExternalIdentifier: id).compactMap { $0 as? EKEvent }
    guard !matches.isEmpty else { fail("no event with id \(id)") }
    guard matches.count == 1 else { fail("id \(id) matches \(matches.count) events; refusing to guess") }
    let event = matches[0]
    if event.hasRecurrenceRules { fail("\"\(event.title ?? "")\" is a repeating event; edit it in Calendar") }
    if !event.calendar.allowsContentModifications { fail("calendar \"\(event.calendar.title)\" is read-only") }
    return event
}

let args = CommandLine.arguments
switch args.count > 1 ? args[1] : "" {
case "calendars":
    printJSON(store.calendars(for: .event).map {
        ["name": $0.title, "writable": $0.allowsContentModifications, "account": $0.source.title]
    })
case "events":
    guard args.count == 4, let start = fmt.date(from: args[2]), let end = fmt.date(from: args[3]) else {
        fail("usage: calhelper events <YYYY-MM-DDTHH:MM> <YYYY-MM-DDTHH:MM>")
    }
    let events = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: nil))
        .sorted { $0.startDate < $1.startDate }
    printJSON(events.map {
        [
            "calendar": $0.calendar.title,
            "title": $0.title ?? "",
            "start": fmt.string(from: $0.startDate),
            "end": fmt.string(from: $0.endDate),
            "all_day": $0.isAllDay,
            "location": $0.location ?? "",
            "id": $0.calendarItemExternalIdentifier ?? "",
            "recurring": $0.hasRecurrenceRules,
        ] as [String: Any]
    })
case "update", "delete":
    guard args.count == (args[1] == "update" ? 4 : 3) else {
        fail("usage: calhelper update <id> <json> | calhelper delete <id>")
    }
    let event = findEvent(args[2])
    if args[1] == "delete" {
        let gone = describe(event)
        do { try store.remove(event, span: .thisEvent) } catch { fail("could not delete: \(error)") }
        printJSON(["deleted": gone])
    } else {
        guard let data = args[3].data(using: .utf8),
              let changes = (try? JSONSerialization.jsonObject(with: data)) as? [String: String] else {
            fail("changes must be a JSON object of strings")
        }
        if let v = changes["title"] { event.title = v }
        if let v = changes["location"] { event.location = v }
        if let v = changes["notes"] { event.notes = v }
        // Moving the start keeps the duration unless a new end is given.
        let duration = event.endDate.timeIntervalSince(event.startDate)
        if let v = changes["start"] {
            guard let d = fmt.date(from: v) else { fail("bad start: \(v)") }
            event.startDate = d
            event.endDate = d.addingTimeInterval(duration)
        }
        if let v = changes["end"] {
            guard let d = fmt.date(from: v) else { fail("bad end: \(v)") }
            event.endDate = d
        }
        if event.endDate <= event.startDate { fail("end must be after start") }
        do { try store.save(event, span: .thisEvent) } catch { fail("could not save: \(error)") }
        printJSON(["updated": describe(event)])
    }
default:
    fail("usage: calhelper calendars | events <start> <end> | update <id> <json> | delete <id>")
}
