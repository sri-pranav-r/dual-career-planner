# Dual-Career Planner for College Athletes

A planner for college athletes balancing sport and studies. It does four jobs:
- flags tournament-vs-exam clashes
- produces the attendance-exemption letter and tracks its sign-off (PED, proctor, HoD), with QR verification for faculty
- reminds athletes before tournaments
- tracks training load, injuries and wellness, without wearables

## Run
    pip install -r requirements.txt
    streamlit run app.py

The app opens at http://localhost:8501 in **demo mode**, with 10 demo athletes. The login page lists the demo accounts:

| Who | Username | Password |
|---|---|---|
| Athlete | `1RV25CS012` (any demo USN) | same as the USN |
| Coach (Cricket) | `coach.cricket` | `planner-demo` |
| Physical Education Director | `ped` | `planner-demo` |
| Proctor / HoD (CSE) | `proctor.cse` / `hod.cse` | `planner-demo` |
| Admin | `admin` | `planner-demo` |

Athletes see the data consent notice the first time they sign in. Demo dates move forward automatically, so there are always upcoming tournaments.

### Before real data goes in
1. Sign in as `admin` and open **Settings**.
2. Enter your college's name, attendance rule, semester dates and letter wording.
3. Under **Switch to real data**, choose a new admin password. This deletes all demo data and every demo login, because their passwords are public.
4. Import the real roster (**Roster import**). Each athlete's first password is their USN, and they must change it at first sign-in. Passwords need 8+ characters.

### QR codes on letters
When a proctor scans the QR code on a letter with a phone, it has to open this app.
- **Automatic.** The app detects the laptop's Wi-Fi address (e.g. `http://192.168.1.20:8501`). The phone must be on the same Wi-Fi.
- **Manual.** Set the address in **Settings → App address**, or with the `PLANNER_BASE_URL` environment variable, for example when the app is deployed to Streamlit Cloud. Settings shows a test QR you can scan.

### Staying signed in
Refreshing keeps you signed in, because a login token is kept in the page address (`?s=...`). It lasts 12 hours, or 30 days if you tick "Keep me signed in". Don't share a signed-in page's address: anyone who opens it is signed in as you until it expires or you sign out.

### Reminders (WhatsApp/SMS)
Reminders always appear in the athlete's in-app Notifications. Nothing is sent outside the app unless `PLANNER_REMINDERS_LIVE=1` is set and a provider is configured:
- **Twilio:** `PLANNER_REMINDER_PROVIDER=twilio`, plus `TWILIO_*`.
- **Webhook:** `PLANNER_REMINDER_PROVIDER=webhook`, plus `PLANNER_REMINDER_WEBHOOK_URL`.

The athlete must also opt in. Reminders run once a day on the first page load, or from cron with `python3 -m features.reminders.run`. See `features/reminders/README.md`.

### Optional
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
- **Admin:** everything, plus Users, Settings, and Privacy and consent (the consent overview and carrying out data-deletion requests).

## Files
- `app.py`: the Streamlit UI: Home pages, the core screens, and routing by role.
- `nav.py`: the menu. Which pages each role gets, their sections, and the line at the top of each page.
- `core.py`: clash detection, session-RPE and ACWR, and the letter .docx.
- `data.py`: schema, migrations, readers and writers, events, the demo seed, and the switch to real data.
- `auth.py`: login, roles, permissions, and remember-me sessions.
- `settings.py`: college rules, letter wording, and the app address.
- `letterdoc.py`: the one path for producing letters (wording plus QR).
- `roster.py`: roster import.
- `hooks.py`: the event bus.
- `features/`: modules that plug in through `INTERFACES.md`:
  - `imports`: timetables and the exam calendar.
  - `letters`: the letter status tracker and notifications.
  - `verification`: QR verification and approvals.
  - `squad`: make-up tests, squad selection, bulk letters, and attendance risk.
  - `records`: calendar, injuries, taper, semester summary, export, and consent/deletion.
  - `reminders`: WhatsApp/SMS reminders, logging nudges, and the team load report.

## Tests
    pip install -r requirements-dev.txt
    python3 -m pytest -q

This covers core, auth, roster, settings, the menu, a headless UI check that signs in as each role and opens every page in its menu, and every feature module.

## How the numbers work
- Session load (AU) = minutes × RPE (Foster's session-RPE).
- ACWR = mean load of the last 7 days ÷ mean load of the last 28 days.
  > 1.5 high risk · 1.3–1.5 caution · 0.8–1.3 sweet spot · < 0.8 under-trained.
- A clash is any timetable slot or CIE/SEE date that falls inside the tournament window, including travel days.
- Attendance risk uses the floor, the on-duty rule and the semester dates from Settings.

## Not built
- Mess and hostel timing conflicts: waiting for the survey to confirm this pain.
- By design: OCR improvements, nutrition, wearables, and video.
