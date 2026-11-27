import EventKit
import Foundation

let store = EKEventStore()
let ready = DispatchSemaphore(value: 0)
nonisolated(unsafe) var allowed = false
nonisolated(unsafe) var permissionError: Error?
store.requestFullAccessToEvents { granted, error in
    allowed = granted
    permissionError = error
    ready.signal()
}
guard ready.wait(timeout: .now() + 90) == .success, allowed else {
    FileHandle.standardError.write(Data("Calendar access unavailable. Check System Settings > Privacy & Security > Calendars. \(permissionError?.localizedDescription ?? "")\n".utf8))
    exit(1)
}

let formatter = ISO8601DateFormatter()
let calendars = store.calendars(for: .event).filter { $0.source.sourceType == .local }
let args = CommandLine.arguments
if args.count == 5, args[1] == "add" {
    guard let start = formatter.date(from: args[2]), let end = formatter.date(from: args[3]),
          end > start, let calendar = calendars.first(where: { $0.allowsContentModifications }) else {
        FileHandle.standardError.write(Data("A writable On My Mac calendar and valid event times are required.\n".utf8))
        exit(2)
    }
    let event = EKEvent(eventStore: store)
    event.calendar = calendar
    event.title = args[4]
    event.startDate = start
    event.endDate = end
    do {
        try store.save(event, span: .thisEvent, commit: true)
        print("Added to \(calendar.title).")
        exit(0)
    } catch {
        FileHandle.standardError.write(Data("\(error.localizedDescription)\n".utf8))
        exit(1)
    }
}

guard args.count == 3, let start = formatter.date(from: args[1]),
      let end = formatter.date(from: args[2]) else { exit(2) }
let events = calendars.isEmpty ? [] : store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: calendars))
let result: [[String: Any]] = events.filter { $0.status != .canceled }.map {
    ["id": $0.eventIdentifier ?? $0.calendarItemIdentifier,
     "title": $0.title ?? "Untitled event", "start": formatter.string(from: $0.startDate),
     "all_day": $0.isAllDay, "date": $0.isAllDay ? localDate($0.startDate) : ""]
}
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result))

func localDate(_ date: Date) -> String {
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd"
    return formatter.string(from: date)
}
