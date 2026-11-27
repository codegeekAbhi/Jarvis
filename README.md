# Jarvis · Today

Jarvis merges your selected Google calendars, local **On My Mac** calendars, and tasks into a daily agenda. The planner and reminder history run on your computer.

## Quick start

Use Python 3.9 or later. Open a terminal in this folder. On Windows, use `py` in place of `python3` if that is how you launch Python.

```sh
python3 jarvis.py today
python3 jarvis.py add "Send the proposal" --at "2027-01-15 10:00"
python3 jarvis.py today --speak
python3 jarvis.py run --lead 15 --brief-at 08:00
```

Replace the example date with your deadline. The last command runs the reminder process. Keep it running in a separate terminal, or configure your OS to start that command when you log in. On Windows Task Scheduler, select **Run only when user is logged on** so notifications can reach the desktop. Stop an interactive process with Ctrl+C. Use `run --once` to check reminders immediately.

## Connect both calendar sources

### Google Calendar · macOS and Windows

1. Enable the **Google Calendar API** in a Google Cloud project. Configure its OAuth consent screen and add your account as a test user if the app is in testing mode.
2. Create a **Desktop app** OAuth client and download its JSON file.
3. Install the connector and sign in through the browser.

```sh
python3 -m pip install -r requirements-google.txt
python3 jarvis.py connect google --credentials "/path/to/client_secret.json"
```

Jarvis reads your primary calendar by default. For multiple calendars, save a JSON array of calendar IDs in `~/.jarvis/google-calendars.json`, for example `["primary", "your-work-calendar-id"]`. You can find the IDs in Google Calendar settings. Google's Calendar read-only consent covers calendar reading. Jarvis fetches events from the configured calendars.

### On My Mac calendars · macOS

Use macOS 14 or later with Apple Command Line Tools and a macOS 14 or later SDK. Install the tools with `xcode-select --install` if needed.

```sh
python3 jarvis.py connect apple
python3 jarvis.py today
```

Run the connection from Terminal and grant Calendar access when macOS asks. If access is denied, check **System Settings → Privacy & Security → Calendars** and retry. Apple requires full access to read events. Jarvis reads events through that grant and writes only when you run an explicit Apple event command. It selects local EventKit sources, so it won't pull the Google calendar mirrored by Apple's Calendar app. iCloud and Exchange calendars shown in Apple Calendar are outside this connector's scope.

## Tasks and alerts

- `today` shows today's events, today's tasks, and overdue tasks. Add `--speak` to hear the rundown.
- `snooze TASK_ID --minutes 10` moves a task's due time. `done TASK_ID` closes it. The agenda shows task IDs.
- The runner checks every 30 seconds, uses the latest event times, and skips canceled events. Timed meetings get advance alerts. All-day items appear in the agenda and summary. Overdue tasks can still alert when the runner starts again.
- The morning notification gives a count. Run `today` for the detailed agenda.

Jarvis stores local data and the Google OAuth token in `~/.jarvis` by default. Set `JARVIS_HOME` to override that directory. Keep the token and database out of Git. Local calendars, tasks, and delivered-alert history stay on their respective machines. Separate Mac and Windows installations can both alert for the same Google event.


## Install the additional skills

```sh
python3 -m pip install -r requirements-skills.txt
```

Run commands from the extracted Jarvis folder. Existing tasks and reminder history are kept when you upgrade. If you previously connected Apple Calendar, run `connect apple` again to compile the updated helper for event creation.

### Read and search local files

```sh
python3 jarvis.py files allow "/path/to/Projects"
python3 jarvis.py files roots
python3 jarvis.py files search "proposal"
python3 jarvis.py files read "/path/to/Projects/proposal.pdf"
python3 jarvis.py files remove "/path/to/Projects"
```

Search covers filenames and content in Markdown, plain text, and PDFs. Results include a local file link and a line or page reference for content matches. Jarvis only reads under folders you allow. Text extraction requires a PDF text layer. Scanned PDFs need OCR first.

### Search Gmail and prepare drafts

Enable the Gmail API in Google Cloud and use a Desktop app OAuth client JSON. Jarvis stores the Gmail token separately from Calendar using Gmail read and compose scopes.

```sh
python3 jarvis.py connect gmail --credentials "/path/to/client_secret.json"
python3 jarvis.py mail search "in:inbox newer_than:7d subject:proposal"
python3 jarvis.py mail summary THREAD_ID
python3 jarvis.py mail draft "recipient@example.com" "Proposal" --body "Here is the update."
python3 jarvis.py mail reply THREAD_ID --body-file "/path/to/reply.txt"
python3 jarvis.py mail send DRAFT_ID
```

The thread ID is at the end of the Gmail link in the search result. The summary includes recent messages' senders and excerpts. The reply command targets the latest incoming message. For plain-text drafts, `mail send` previews the recipients, subject, and body, then requires you to type `SEND`. Review HTML drafts and drafts with attachments in Gmail.

To have Jarvis write the body, run Ollama locally, set `JARVIS_OLLAMA_MODEL` to an installed local model, and replace `--body` with `--prompt "Confirm Friday and ask for the agenda"`. Jarvis gives the local model the subject and recent thread excerpts and saves the result to Gmail as a draft. Review the generated text before sending.

### Create events and schedule inbox summaries

```sh
python3 jarvis.py connect google-write --credentials "/path/to/client_secret.json"
python3 jarvis.py event "Project planning" --calendar google --at "2027-01-15 10:00" --minutes 45
python3 jarvis.py event "Focus time" --calendar apple --at "2027-01-15 14:00"
python3 jarvis.py jobs add "Weekday inbox" --at 08:30 --cadence weekdays
python3 jarvis.py jobs list
python3 jarvis.py jobs show "Weekday inbox"
```

The Google write connection is separate from the read-only agenda connection. Event creation uses the Google primary calendar or the first writable On My Mac calendar. Dates and schedule times are interpreted in your computer's local time.

`run` also runs scheduled inbox jobs and stores results in SQLite. Jobs produce one local summary per eligible date. If your computer wakes after the scheduled time, Jarvis runs today's job once and skips prior dates. An unsuccessful Gmail request is retried on the next check. Keep the runner active separately or configure it at login as described above. The agenda UI is the CLI, so you can close the terminal used to query it while leaving the runner active separately.

### Texts and call follow-ups

Use an SMS- and voice-capable Twilio number. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `JARVIS_PHONE_NUMBER` in your environment. Set your Jarvis number in E.164 format, such as `+15551234567`. The carrier and your Twilio account determine which destinations you can reach.

```sh
python3 jarvis.py phone texts
python3 jarvis.py phone reply MESSAGE_SID --body "Thanks, I'll follow up tomorrow."
python3 jarvis.py phone calls
python3 jarvis.py phone call-text CALL_SID --body "I saw your call. When are you free?"
python3 jarvis.py phone callback CALL_SID --body "Hi, this is Jarvis calling back on Abhishek's behalf."
```

The connector lists incoming texts and call records for the Twilio number. Text replies and callbacks require explicit confirmation after preview. A callback reads the provided message aloud. This release uses a dedicated number and supports call follow-ups. Live answering, two-way voice conversations, and access to personal iMessage or cellular call history are outside this release.
