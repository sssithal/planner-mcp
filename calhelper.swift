// calhelper: fast, read-only access to macOS calendars via EventKit.
// server.py compiles this on first use (swiftc ships with Xcode Command Line Tools).
//
//   calhelper calendars
//   calhelper events 2026-10-01T00:00 2026-10-08T00:00
//
// Prints JSON to stdout. Recurring events are expanded into individual occurrences.
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
        ] as [String: Any]
    })
default:
    fail("usage: calhelper calendars | calhelper events <start> <end>")
}
