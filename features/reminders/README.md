# reminders

Reminders outside the app, logging nudges, and the weekly team load report.

- **Tournament reminders**: 10 days and again 3 days before an athlete's first away day, e.g.
  "VTU Cricket starts in 10 days. It clashes with 3 tests and 12 classes. You have no exemption letter yet. Generate it now in the planner. <link>".
  The letter line follows `features.letters` (drafted, rejected, needs redo, approved...). A moved tournament gets fresh reminders.
- **Logging nudges**: no session logged for 3 days, then again every 7 days of silence. Athletes who never logged are nudged weekly.
- **Weekly team load report** (per sport): sessions, 7-day load, ACWR and zone, last logged. Flags "spiked" (ACWR > 1.3; under
  28 days of history, this week > 1.5x the average earlier week) and "not logging" (3+ days). Built every Monday by the daily run,
  or live in the "Team load report" tab, with a CSV download. Coaches see their sport; the PED and admin see all.

Every reminder goes to the athlete's in-app Notifications (`features.letters.logic.notify`) and to `reminders_outbox`.
A dedupe key per message makes every run safe to repeat.

## Sending (dry run by default)
`senders.py`. Nothing leaves the app unless `PLANNER_REMINDERS_LIVE=1` **and** a provider is configured:
- `PLANNER_REMINDER_PROVIDER=twilio` with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_SMS_FROM`, `TWILIO_WHATSAPP_FROM`
- `PLANNER_REMINDER_PROVIDER=webhook` with `PLANNER_REMINDER_WEBHOOK_URL` (+ optional `PLANNER_REMINDER_WEBHOOK_TOKEN`); gets POST `{"channel","to","text"}`
- `PLANNER_REMINDER_CHANNEL` = `whatsapp` (default) or `sms`, for athletes who haven't picked one.

Live mode also needs the athlete's opt-in (Reminder settings tab) and a valid mobile number; otherwise the row is `skipped`.
In dry run the outbox shows each message as `dry_run` with a note on what live mode would skip.

## Running it
- In the app: PED/admin press "Run today's reminders" in the Reminders tab.
- Daily: core calls `logic.run_if_due(con)` on app start (runs at most once a day), and/or cron:
  `0 7 * * *  cd planner && python3 -m features.reminders.run` (`--force`, `--report`, `--today YYYY-MM-DD`, `--db PATH`).

## Tabs
Reminders (coach, admin), Team load report (coach, admin), Reminder settings (athlete: consent + channel).

## Tables
`reminders_outbox`, `reminders_prefs`, `reminders_reports`, `reminders_runs`.

## Hooks
Handles `athlete_deleted`: removes the athlete's outbox rows and prefs, and scrubs them from saved reports. Emits nothing.
Links use `settings.base_url(con)` (falls back to env `PLANNER_BASE_URL`).
