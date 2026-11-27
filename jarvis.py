"""Local calendar planner and reminder runner."""
import argparse
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time as clock
import uuid

import skills

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
UTC = timezone.utc
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def utc_string(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()


def database():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(DATA / "planner.db")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, due TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS delivered (
            id TEXT PRIMARY KEY, delivered_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources (name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS jobs (
            name TEXT PRIMARY KEY, at TEXT NOT NULL, query TEXT NOT NULL,
            cadence TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
            name TEXT NOT NULL, day TEXT NOT NULL, body TEXT NOT NULL,
            PRIMARY KEY (name, day)
        );
    """)
    return connection


def google_events(start, end):
    service = skills.google_service(DATA, "google-token.json", GOOGLE_SCOPES, "calendar", "v3")
    selection = DATA / "google-calendars.json"
    calendar_ids = json.loads(selection.read_text(encoding="utf-8")) if selection.exists() else ["primary"]
    if not isinstance(calendar_ids, list) or any(not isinstance(item, str) for item in calendar_ids):
        raise ValueError("google-calendars.json must contain a list of calendar IDs")
    for calendar_id in dict.fromkeys(calendar_ids):
        request = service.events().list(
            calendarId=calendar_id, timeMin=utc_string(start), timeMax=utc_string(end),
            singleEvents=True, showDeleted=False,
        )
        while request is not None:
            page = request.execute(num_retries=1)
            for event in page.get("items", []):
                if event.get("status") == "cancelled" or "start" not in event:
                    continue
                if any(person.get("self") and person.get("responseStatus") == "declined"
                       for person in event.get("attendees", [])):
                    continue
                event_start = event["start"]
                original = event.get("originalStartTime", event_start)
                occurrence = original.get("dateTime") or original.get("date")
                yield {
                    "id": f"google:{event.get('iCalUID', event['id'])}:{occurrence}",
                    "title": event.get("summary", "Untitled event"),
                    "start": event_start.get("dateTime"),
                    "date": event_start.get("date"),
                    "all_day": "date" in event_start,
                }
            request = service.events().list_next(request, page)


def apple_events(start, end):
    helper = DATA / "jarvis-apple-calendar"
    if not helper.exists():
        raise RuntimeError("Run python jarvis.py connect apple first")
    result = subprocess.run(
        [str(helper), utc_string(start), utc_string(end)],
        capture_output=True, text=True, timeout=100,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Apple Calendar helper failed")
    for event in json.loads(result.stdout):
        event["id"] = "apple:" + event["id"]
        yield event


def calendar_events(connection, start, end):
    events = {}
    errors = []
    if (DATA / "google-token.json").exists():
        try:
            events.update((event["id"], event) for event in google_events(start, end))
        except Exception as error:
            errors.append(f"Google Calendar: {error}")
    if sys.platform == "darwin" and connection.execute(
        "SELECT 1 FROM sources WHERE name = 'apple'"
    ).fetchone():
        try:
            for event in apple_events(start, end):
                events[event["id"]] = event
        except Exception as error:
            errors.append(f"Apple Calendar: {error}")
    return list(events.values()), errors


def connect(connection, args):
    if args.provider in {"google", "google-write", "gmail"}:
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not args.credentials:
            raise ValueError("Supply the OAuth Desktop app JSON with --credentials")
        scopes = {"google": GOOGLE_SCOPES, "google-write": skills.GOOGLE_WRITE_SCOPE,
                  "gmail": skills.GMAIL_SCOPES}[args.provider]
        flow = InstalledAppFlow.from_client_secrets_file(args.credentials, scopes)
        credentials = flow.run_local_server(host="127.0.0.1", port=0)
        token = DATA / f"{args.provider}-token.json"
        with os.fdopen(os.open(token, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as file:
            file.write(credentials.to_json())
    else:
        if sys.platform != "darwin":
            raise ValueError("Local Apple Calendar is available on macOS")
        helper = DATA / "jarvis-apple-calendar"
        subprocess.run(
            ["swiftc", "-module-cache-path", str(DATA / "swift-cache"),
             "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
             "-Xlinker", str(ROOT / "AppleCalendarInfo.plist"),
             str(ROOT / "apple_calendar.swift"), "-o", str(helper)], check=True
        )
        now = datetime.now(UTC)
        list(apple_events(now, now + timedelta(days=1)))
        with connection:
            connection.execute("INSERT OR IGNORE INTO sources VALUES ('apple')")
    print(f"Connected {args.provider}.")


def today(connection, spoken):
    current = date.today()
    start = datetime.combine(current, time.min).astimezone()
    end = datetime.combine(current + timedelta(days=1), time.min).astimezone()
    events, errors = calendar_events(connection, start, end)
    print(f"Today · {current:%A, %B %d}")
    rundown = []
    for event in sorted(events, key=lambda item: (
        not item["all_day"], local_time(item["start"]) if item["start"] else start
    )):
        if event["all_day"]:
            label = "All day"
        else:
            label = local_time(event["start"]).strftime("%H:%M")
        rundown.append(f"{label}, {event['title']}")
        print(f"  {label:>7}  {event['title']}  ({event['id'].split(':', 1)[0]})")
    for task_id, title, due in connection.execute(
        "SELECT id, title, due FROM tasks WHERE done=0 AND due < ? ORDER BY due", (utc_string(end),)
    ):
        when = local_time(due)
        label = "Overdue" if when < datetime.now().astimezone() else when.strftime("%H:%M")
        rundown.append(f"{label}, {title}")
        print(f"  {label:>7}  {title}  [task {task_id}]")
    for error in errors:
        print(error, file=sys.stderr)

    if spoken:
        speech = ". ".join(rundown) if rundown else "Your agenda is empty."
        if errors:
            speech += " Some calendar data was unavailable."
        if sys.platform == "darwin":
            subprocess.run(["say", "-f", "-"], input=speech, text=True, check=True)
        elif sys.platform == "win32":
            script = """
            $ErrorActionPreference = 'Stop'
            Add-Type -AssemblyName System.Speech
            $voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $voice.Speak($env:JARVIS_SPEECH)
            $voice.Dispose()
            """
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                           env=dict(os.environ, JARVIS_SPEECH=speech), check=True)
        else:
            print(speech)


def notify(title, message):
    if sys.platform == "darwin":
        script = """on run argv
            display notification (item 2 of argv) with title (item 1 of argv)
        end run"""
        subprocess.run(["osascript", "-e", script, title, message], check=True, timeout=15)
    elif sys.platform == "win32":
        # Keep user-provided text in environment variables, outside the script.
        script = """
        $ErrorActionPreference = 'Stop'
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $tray = New-Object System.Windows.Forms.NotifyIcon
        $tray.Icon = [System.Drawing.SystemIcons]::Information
        $tray.Visible = $true
        $tray.ShowBalloonTip(5000, $env:JARVIS_ALERT_TITLE, $env:JARVIS_ALERT_BODY,
            [System.Windows.Forms.ToolTipIcon]::Info)
        Start-Sleep -Seconds 6
        $tray.Dispose()
        """
        env = dict(os.environ, JARVIS_ALERT_TITLE=title, JARVIS_ALERT_BODY=message)
        subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                       env=env, check=True, timeout=20)
    else:
        print(f"{title}: {message}")


def deliver(connection, key, title, message, now):
    # Reserve the alert within one transaction so two runners cannot post it twice.
    with connection:
        inserted = connection.execute(
            "INSERT OR IGNORE INTO delivered VALUES (?, ?)", (key, utc_string(now))
        ).rowcount
        if inserted:
            notify(title, message)


def check_reminders(connection, lead, brief_at):
    now = datetime.now().astimezone()
    events, errors = calendar_events(connection, now, now + timedelta(minutes=lead + 5))
    for error in errors:
        print(error, file=sys.stderr)
    for event in events:
        if event["all_day"]:
            continue
        start = local_time(event["start"])
        if now <= start <= now + timedelta(minutes=lead):
            deliver(connection, f"{event['id']}:{utc_string(start)}", "Jarvis · Upcoming",
                    f"{start:%H:%M} · {event['title']}", now)
    for task_id, title, due in connection.execute(
        "SELECT id, title, due FROM tasks WHERE done=0 AND due <= ? ORDER BY due", (utc_string(now),)
    ).fetchall():
        deliver(connection, f"task:{task_id}:{due}", "Jarvis · Task due", title, now)
    if brief_at and time.fromisoformat(brief_at) <= now.time():
        key = f"brief:{now.date()}"
        if not connection.execute("SELECT 1 FROM delivered WHERE id = ?", (key,)).fetchone():
            end = datetime.combine(now.date() + timedelta(days=1), time.min).astimezone()
            agenda, agenda_errors = calendar_events(connection, now, end)
            open_tasks = connection.execute(
                "SELECT count(*) FROM tasks WHERE done=0 AND due < ?", (utc_string(end),)
            ).fetchone()[0]
            suffix = " (calendar sync incomplete)" if agenda_errors else ""
            deliver(connection, key, "Jarvis · Today",
                    f"{len(agenda)} calendar items remaining and {open_tasks} tasks due{suffix}. Run Jarvis Today for details.", now)


def run_jobs(connection, now):
    day = now.date().isoformat()
    for name, at, query, cadence, created_at in connection.execute(
        "SELECT name, at, query, cadence, created_at FROM jobs"
    ).fetchall():
        if cadence == "weekdays" and now.weekday() >= 5:
            continue
        scheduled = datetime.combine(now.date(), time.fromisoformat(at)).astimezone()
        if now < scheduled:
            continue
        exists = connection.execute(
            "SELECT 1 FROM reports WHERE name=? AND day=?", (name, day)
        ).fetchone()
        if not exists:
            try:
                body = skills.inbox_summary(DATA, query, 5)
            except Exception as error:
                print(f"Inbox job {name} failed and will retry: {error}", file=sys.stderr)
                continue
            with connection:
                # Guard against an edited or removed job while Gmail was fetching.
                stored = connection.execute(
                    "INSERT OR IGNORE INTO reports SELECT name, ?, ? FROM jobs "
                    "WHERE name=? AND created_at=? AND at=? AND query=? AND cadence=?",
                    (day, body, name, created_at, at, query, cadence),
                ).rowcount
            if not stored:
                continue
        deliver(connection, f"job:{name}:{day}", "Jarvis · Inbox summary",
                f"{name} is ready. Open it with the Jarvis jobs show command.", now)


def jobs_command(connection, args):
    if args.action == "add":
        at = datetime.strptime(args.at, "%H:%M").strftime("%H:%M")
        with connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
                (args.name, at, args.query, args.cadence, datetime.now(UTC).isoformat()),
            )
        print(f"Scheduled {args.name} at {at} ({args.cadence}).")
    elif args.action == "list":
        for name, at, cadence in connection.execute("SELECT name, at, cadence FROM jobs ORDER BY name"):
            print(f"{name} · {at} · {cadence}")
    elif args.action == "remove":
        with connection:
            if not connection.execute("DELETE FROM jobs WHERE name=?", (args.name,)).rowcount:
                raise ValueError("Job not found")
        print(f"Removed {args.name}. Existing summaries are still available.")
    else:
        report = connection.execute(
            "SELECT day, body FROM reports WHERE name=? ORDER BY day DESC LIMIT 1", (args.name,)
        ).fetchone()
        if not report:
            raise ValueError("No summary is available for that job")
        print(skills.safe_display(f"{report[0]}\n{report[1]}"))


def add_skill_commands(commands):
    files = commands.add_parser("files").add_subparsers(dest="action", required=True)
    for action in ("allow", "remove", "read"):
        files.add_parser(action).add_argument("path")
    files.add_parser("roots")
    search = files.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, choices=range(1, 101), default=15, metavar="1-100")

    mail = commands.add_parser("mail").add_subparsers(dest="action", required=True)
    search = mail.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, choices=range(1, 51), default=5, metavar="1-50")
    mail.add_parser("summary").add_argument("thread")
    mail.add_parser("send").add_argument("draft")
    draft = mail.add_parser("draft")
    draft.add_argument("to")
    draft.add_argument("subject")
    reply = mail.add_parser("reply")
    reply.add_argument("thread")
    for command in (draft, reply):
        body = command.add_mutually_exclusive_group(required=True)
        body.add_argument("--body")
        body.add_argument("--body-file")
        body.add_argument("--prompt", help="Write a draft with a local Ollama model")

    event = commands.add_parser("event")
    event.add_argument("title")
    event.add_argument("--calendar", choices=["apple", "google"], required=True)
    event.add_argument("--at", required=True)
    event.add_argument("--minutes", type=int, default=30)

    jobs = commands.add_parser("jobs").add_subparsers(dest="action", required=True)
    add = jobs.add_parser("add")
    add.add_argument("name")
    add.add_argument("--at", required=True, help="Local time in HH:MM format")
    add.add_argument("--cadence", choices=["daily", "weekdays"], default="weekdays")
    add.add_argument("--query", default="in:inbox newer_than:1d")
    jobs.add_parser("list")
    jobs.add_parser("show").add_argument("name")
    jobs.add_parser("remove").add_argument("name")

    phone = commands.add_parser("phone").add_subparsers(dest="action", required=True)
    for action in ("texts", "calls"):
        phone.add_parser(action).add_argument("--limit", type=int, choices=range(1, 101),
                                             default=10, metavar="1-100")
    for action, field in (("reply", "message"), ("call-text", "call"), ("callback", "call")):
        reply = phone.add_parser(action)
        reply.add_argument(field)
        reply.add_argument("--body", required=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("connect")
    setup.add_argument("provider", choices=["google", "google-write", "gmail", "apple"])
    setup.add_argument("--credentials")
    agenda = commands.add_parser("today")
    agenda.add_argument("--speak", action="store_true")
    add = commands.add_parser("add")
    add.add_argument("title")
    add.add_argument("--at", required=True, help="Local date and time in YYYY-MM-DD HH:MM format")
    done = commands.add_parser("done")
    done.add_argument("id")
    snooze = commands.add_parser("snooze")
    snooze.add_argument("id")
    snooze.add_argument("--minutes", type=int, default=10)
    run = commands.add_parser("run")
    run.add_argument("--lead", type=int, default=15)
    run.add_argument("--brief-at", default="08:00")
    run.add_argument("--once", action="store_true")
    add_skill_commands(commands)
    args = parser.parse_args()
    with closing(database()) as connection:
        if args.command == "connect":
            connect(connection, args)
        elif args.command == "files":
            skills.file_command(DATA, args)
        elif args.command == "mail":
            skills.mail_command(DATA, args)
        elif args.command == "event":
            skills.add_calendar_event(DATA, args)
        elif args.command == "phone":
            skills.phone_command(args)
        elif args.command == "jobs":
            jobs_command(connection, args)
        elif args.command == "today":
            today(connection, args.speak)
        elif args.command == "add":
            due = datetime.strptime(args.at, "%Y-%m-%d %H:%M").astimezone()
            task_id = uuid.uuid4().hex[:10]
            with connection:
                connection.execute("INSERT INTO tasks (id,title,due) VALUES (?,?,?)",
                                   (task_id, args.title, utc_string(due)))
            print(f"Added {task_id}: {args.title} at {due:%Y-%m-%d %H:%M %Z}")
        elif args.command in {"done", "snooze"}:
            with connection:
                if args.command == "done":
                    updated = connection.execute(
                        "UPDATE tasks SET done=1 WHERE id=? AND done=0", (args.id,)
                    ).rowcount
                else:
                    if args.minutes < 1:
                        parser.error("--minutes must be positive")
                    due = datetime.now(UTC) + timedelta(minutes=args.minutes)
                    updated = connection.execute(
                        "UPDATE tasks SET due=? WHERE id=? AND done=0", (utc_string(due), args.id)
                    ).rowcount
                if not updated:
                    parser.error("No open task with that ID")
            print(f"{args.command.title()}: {args.id}")
        else:
            if args.lead < 1:
                parser.error("--lead must be positive")
            time.fromisoformat(args.brief_at)
            while True:
                try:
                    check_reminders(connection, args.lead, args.brief_at)
                    run_jobs(connection, datetime.now().astimezone())
                except (OSError, subprocess.SubprocessError, sqlite3.Error) as error:
                    if args.once:
                        raise
                    print(f"Reminder check failed: {error}", file=sys.stderr)
                if args.once:
                    break
                clock.sleep(30)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ImportError, OSError, ValueError, RuntimeError, sqlite3.Error, subprocess.SubprocessError) as error:
        sys.exit(f"Jarvis: {error}")
