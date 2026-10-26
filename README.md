# Dual-Career Planner for College Athletes

A planner for college athletes balancing sport and studies, for the athletes themselves, their coaches, the Physical Education Director (PED) and the faculty who approve their leave. It:
- flags tournament-vs-exam clashes
- produces the leave and attendance-compensation letter to the Dean of Student Affairs (or the Principal), copied to the student's faculty, with an email version of the same text, and tracks its sign-off (PED confirms, proctor and HoD sign, the Dean approves), with QR verification for faculty
- projects attendance against the college rule (by default 85% required, and down to 75% with the Dean's permission; both are set in the app)
- reminds athletes before tournaments
- tracks training load, injuries and wellness, without wearables

It is a Streamlit app with a single SQLite database. A new deployment starts empty: there are no sample records, no built-in accounts and no default passwords.

## Deploy on Streamlit Community Cloud

1. **Create the app.** At [share.streamlit.io](https://share.streamlit.io), choose **Create app**, pick this repository, branch `main` and main file `app.py`. Python 3.11 or newer.
2. **Add secrets** before the first start: **Advanced settings → Secrets** (or later, **Manage app → Settings → Secrets**). Paste TOML like this, with your own values:

   ```toml
   PLANNER_SETUP_CODE = "pick-a-long-random-phrase"
   PLANNER_SECRET = "another-long-random-string-for-letter-qr-codes"
   PLANNER_BASE_URL = "https://your-app-name.streamlit.app"
   ```

   | Secret | Needed | What it does |
   |---|---|---|
   | `PLANNER_SETUP_CODE` | Recommended | Asked for when creating the first admin, so nobody else who finds the address first can. Without it, the app writes a fresh code to the log at every start. |
   | `PLANNER_SECRET` | Recommended | Signs the QR codes on letters. Keep it the same forever: if it changes, letters printed earlier no longer verify. Without it, a key is generated and kept in the database (and travels with backups). |
   | `PLANNER_BASE_URL` | Yes, once you know the address | The https address people open. QR codes on letters and links in reminders point here. It can also be set in **Settings → App address**. |
   | `PLANNER_DB` | Self-hosting only | Where the database file lives (default `planner.db` next to `app.py`). |
   | `PLANNER_REMINDERS_LIVE`, `PLANNER_REMINDER_PROVIDER`, `TWILIO_*`, `PLANNER_REMINDER_WEBHOOK_*` | Optional | Sending reminders by SMS or WhatsApp. See [Reminders](#reminders-whatsapp-or-sms). |

   Any of these can also be an environment variable; an environment variable wins over a secret.
3. **Deploy**, then open the app. It shows **Set up the planner**.
4. **Create the first admin.** Enter the setup code (the `PLANNER_SETUP_CODE` you chose, or the code on the `Planner setup code:` line in **Manage app → logs**), the college name, a username such as `sports.office`, and a password of at least 12 characters. You are signed in as that admin.
5. **Set the college up** in **Settings**: attendance rules, semester dates, letter wording, and the app address if `PLANNER_BASE_URL` isn't set. Read the **Privacy policy** and **Terms of use** (Help, and the login page); they use the college name from Settings. Edit `legal.py` if your college needs different wording.
6. **Add staff** on **Users**: coaches (a coach with no sport is the PED), faculty (teacher, proctor or HoD, scoped to a department and optionally a semester and section) and other admins. Each new account gets a one-time password, shown once, to hand over privately.
7. **Import the roster** on **Roster import** (.csv or .xlsx; download the blank template there). Every new athlete gets a login: their USN as the username and a one-time password. The passwords are shown once after the import, with a CSV download, so you can hand them out.
8. **Download a backup** (Settings → Backup and restore). See the next section before you rely on the app.

Everyone signs in with the one-time password they were given and must choose their own (10+ characters; 12+ for admins) before they can continue.

### Keeping the data: backups
Streamlit Community Cloud does not keep files between restarts. The database is lost when the app is redeployed, rebooted, or put to sleep after a few days without visitors. So:
- An admin should download a backup at least once a week and after big changes, in **Settings → Backup and restore**. Home reminds admins when the last backup is more than 7 days old. The backup is the whole database (personal and health data included), so keep it somewhere private.
- After the app has lost its data, it shows **Set up the planner** again. Choose **Restore a backup**, enter the setup code and upload the latest backup. Everyone's accounts come back as they were in the backup.
- For a pilot with real athletes, prefer a host with a persistent disk (below), or at least keep the app awake and the backups regular.

### Hosting on a server with a persistent disk
Any machine that runs Python works (a college server, or a cloud VM):

    pip install -r requirements.txt
    export PLANNER_DB=/var/lib/planner/planner.db
    export PLANNER_SETUP_CODE=...   PLANNER_SECRET=...   PLANNER_BASE_URL=https://planner.example.edu
    streamlit run app.py --server.port 8501

Put it behind a reverse proxy (for example nginx) that serves HTTPS and forwards WebSocket connections. Back up the database file with a nightly copy, or download backups from Settings. The folder in `PLANNER_DB` is created if it is missing.

### Custom domain
On Streamlit Cloud the app gets a `*.streamlit.app` address. To use the college's own domain, either run it on a server as above and point a subdomain at it, or keep Streamlit Cloud and have the college domain redirect to it. Whichever address people use, put it in `PLANNER_BASE_URL` (or Settings → App address) so QR codes on letters open it. Streamlit's own menu, deploy button and footer are hidden by the app; the small "Hosted with Streamlit" badge that Streamlit Cloud adds outside the app can't be removed from inside it.

## Security
- **No default accounts or passwords.** The first admin is created at setup, behind the setup code. Everyone else starts with a random one-time password and must change it at first sign-in. An admin can reset a password (Users), which makes a new one-time password and signs the person out everywhere.
- **Passwords** are stored as PBKDF2-SHA256 hashes with a per-user salt. They need 10+ characters (12+ for admins), can't contain the username and can't be a common password.
- **Lock-out.** Five wrong passwords in a row lock that username for 15 minutes. The setup code has the same limit.
- **Sign-in sessions** use a random token kept in a cookie (SameSite=Strict, Secure on https). Only a hash of it is stored. It lasts 12 hours, or 30 days with "Keep me signed in". Links people copy never contain it. Changing your password signs out your other devices; **Account → Sign out on every device** does it on request.
- **The last admin** can't be deleted or demoted.
- **Errors** show a short message with a reference; the details go to the server log, never to the page.
- **Secrets** come from Streamlit secrets or environment variables only. `planner.db`, `.streamlit/secrets.toml` and `.env` are in `.gitignore`; never commit them.
- **Letters** carry a signed QR code. Faculty scanning it see who the letter is for and can approve it after signing in.

## Run it on your own computer
    pip install -r requirements.txt
    streamlit run app.py

It opens at http://localhost:8501 on an empty database (`planner.db`) and asks for the setup code, which it prints in the terminal. To start again from nothing, stop the app and delete `planner.db`.

A database made by a version before v7 held made-up sample records and logins with public passwords. The app refuses to use one: it offers a copy to download and then clears it.

### QR codes on a local network
When a proctor scans the QR code on a letter, it has to open this app. On a laptop, the app detects its Wi-Fi address (e.g. `http://192.168.1.20:8501`) and the phone must be on the same Wi-Fi. Settings shows a test QR you can scan.

### Reminders (WhatsApp or SMS)
Reminders always appear in the athlete's in-app Notifications. Nothing is sent outside the app unless `PLANNER_REMINDERS_LIVE=1` is set and a provider is configured, as secrets or environment variables:
- **Twilio:** `PLANNER_REMINDER_PROVIDER=twilio`, plus `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and `TWILIO_SMS_FROM` or `TWILIO_WHATSAPP_FROM`.
- **Webhook:** `PLANNER_REMINDER_PROVIDER=webhook`, plus `PLANNER_REMINDER_WEBHOOK_URL` (and optionally `PLANNER_REMINDER_WEBHOOK_TOKEN`).

The athlete must also opt in. Reminders run once a day on the first page load, or from cron with `python3 -m features.reminders.run`. See `features/reminders/README.md`.

### Photo timetables
Photo timetables need the Tesseract OCR program (`apt install tesseract-ocr`, `brew install tesseract`, or the Windows installer). Streamlit Cloud installs it from `packages.txt`.

## Finding your way around
Everything sits in a menu on the left, grouped into a few sections. On a phone the menu is tucked behind the arrow at the top left, and a **Go to** list sits at the top of every page. Each page opens with a line saying what it shows and what to do there, and **Help** lists every page for your role.

Every role starts on **Home**. It shows what needs your attention, such as a letter waiting for your signature, a load spike or a missed test with no make-up request, with a button to the right page. A short **Start here** list covers first-time setup. The page in view is kept in the address (`?p=...`), so a refresh stays on it.

## Who sees what
- **Athlete:** their own data only.
  - **Today:** Home, Next 14 days, Calendar, Notifications.
  - **Academics:** Clashes, Exemption letters (download, sign-off progress, faculty decisions), Make-up tests, Attendance, Timetable and tests.
  - **Training and health:** Log training, Training load (load and wellness), Injuries, Taper plan.
  - **You:** Semester summary, Reminder settings, Privacy and data, Help.
- **Coach:** athletes in their sport.
  - **Overview:** Home, Squad, Athlete detail, Calendar.
  - **Tournaments and letters:** Tournaments (athletes are notified when one changes), Squad selection with a clash preview, Letters (sign-off board, faculty decisions, bulk download), Reminders.
  - **Academics:** Make-up tests, Attendance risk.
  - **Training and health:** Next 14 days, Team load report, Injuries, Taper plans, Pilot results.
  - **Reports and setup:** Semester summary, Export, Roster import, Help.
- **PED** (a coach with no sport): all sports, and signs letters at the PED step.
- **Faculty** (teacher, proctor or HoD): their department, optionally narrowed to a semester and section.
  - Home, Upcoming absences, Athlete detail (clashes and letters only), Calendar, Letters, Make-up tests, Attendance risk, Import exam calendar, Import timetable, Semester summary, Export and Help.
  - Faculty never see injuries, wellness or training load.
- **Admin:** everything, plus Users, Settings (including backup and restore), and Privacy and consent (the consent overview and carrying out data-deletion requests).

## Files
- `app.py`: the Streamlit UI: first-run setup, Home pages, the core screens, and routing by role.
- `firstrun.py`: the setup code and creating the first admin (or restoring a backup) on an empty database.
- `config.py`: deployment settings from environment variables or Streamlit secrets.
- `nav.py`: the menu. Which pages each role gets, their sections, and the line at the top of each page.
- `core.py`: clash detection, session-RPE and ACWR, and the letter .docx.
- `data.py`: schema, migrations, readers and writers, events, backup and restore.
- `auth.py`: login, lock-out, one-time passwords, roles, permissions, and sign-in sessions.
- `settings.py`: college rules, letter wording, and the app address.
- `letterdoc.py`: the one path for producing letters (wording plus QR) and their email version.
- `legal.py`: the privacy policy and terms of use.
- `roster.py`: roster import.
- `hooks.py`: the event bus.
- `assets/favicon.png`: the browser tab icon.
- `.streamlit/config.toml`: Streamlit settings (no error details on the page, upload limit, no usage statistics).
- `features/`: modules that plug in through `INTERFACES.md`:
  - `imports`: timetables and the exam calendar.
  - `letters`: the letter status tracker and notifications.
  - `verification`: QR verification and approvals.
  - `squad`: make-up tests, squad selection, bulk letters, and attendance risk.
  - `records`: calendar, injuries, taper, semester summary, export, and consent/deletion.
  - `reminders`: WhatsApp/SMS reminders, logging nudges, and the team load report.
- `testkit.py`: made-up athletes, tournaments and logins that only the tests use. The app never loads it.

## Tests
    pip install -r requirements-dev.txt
    python3 -m pytest -q

This covers core, auth (lock-out, one-time passwords, sessions), first-run setup, backup and restore, roster, settings, the menu, a headless UI check that sets up an empty planner and then signs in as each role and opens every page in its menu, and every feature module. The tests build their own databases from `testkit.py`; they never touch `planner.db`.

## How the numbers work
- Session load (AU) = minutes × RPE (Foster's session-RPE).
- ACWR = mean load of the last 7 days ÷ mean load of the last 28 days.
  Above 1.5 high risk · 1.3 to 1.5 caution · 0.8 to 1.3 sweet spot · below 0.8 under-trained.
- A clash is any timetable slot or CIE/SEE date that falls inside the tournament window, including travel days.
- Attendance risk uses the floor, the on-duty rule and the semester dates from Settings.

## Not built
- Mess and hostel timing conflicts: ruled out by the athlete survey.
- By design: OCR improvements, nutrition, wearables, and video.
