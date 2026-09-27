"""
Pluggable message senders for reminders that leave the app.

The default is DryRunSender: every message is written to the outbox table and
shown in the Reminders tab, and nothing is sent to anyone. A real provider is
used only when BOTH of these are set:

  PLANNER_REMINDERS_LIVE=1
  PLANNER_REMINDER_PROVIDER=twilio | webhook

Twilio (SMS and WhatsApp):
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
  TWILIO_SMS_FROM        e.g. +1415xxxxxxx
  TWILIO_WHATSAPP_FROM   e.g. +14155238886 (the Twilio sandbox number)

Webhook (any gateway: MSG91, Gupshup, a college server...):
  PLANNER_REMINDER_WEBHOOK_URL    receives POST {"channel", "to", "text"} as JSON
  PLANNER_REMINDER_WEBHOOK_TOKEN  optional, sent as "Authorization: Bearer <token>"

PLANNER_REMINDER_CHANNEL (whatsapp | sms, default whatsapp) is the channel for
athletes who haven't picked one. Live sending also needs each athlete's consent
(reminders_prefs.external_ok), which logic.py checks.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request

CHANNELS = ("whatsapp", "sms")


def normalise_phone(raw) -> str | None:
    """Indian mobile numbers to +91XXXXXXXXXX; other +country numbers kept; anything else None."""
    if raw is None:
        return None
    s = str(raw).strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if plus and 11 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return "+" + digits
    return None


class DryRunSender:
    live = False

    def __init__(self, reason: str = "No provider configured"):
        self.reason = reason
        self.name = f"Dry run: nothing leaves the app ({reason})"

    def send(self, channel: str, to: str, text: str) -> tuple[bool, str]:
        return True, "dry run, not sent"


class TwilioSender:
    live = True
    name = "Twilio (live)"

    def __init__(self, sid: str, token: str, sms_from: str = "", whatsapp_from: str = "", opener=None):
        self.sid, self.token = sid, token
        self.sms_from, self.whatsapp_from = sms_from, whatsapp_from
        self._open = opener or urllib.request.urlopen

    def send(self, channel: str, to: str, text: str) -> tuple[bool, str]:
        frm = self.whatsapp_from if channel == "whatsapp" else self.sms_from
        if not frm:
            return False, f"no Twilio sender number for {channel}"
        if channel == "whatsapp":
            to, frm = f"whatsapp:{to}", f"whatsapp:{frm}"
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json",
            data=urllib.parse.urlencode({"To": to, "From": frm, "Body": text}).encode(),
            headers={"Authorization": "Basic " + base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()},
        )
        try:
            with self._open(req, timeout=15) as r:
                return True, json.loads(r.read() or b"{}").get("sid", "sent")
        except Exception as err:  # noqa: BLE001 - one failed number must not stop the run
            return False, str(err)[:300]


class WebhookSender:
    live = True
    name = "Webhook (live)"

    def __init__(self, url: str, token: str = "", opener=None):
        self.url, self.token = url, token
        self._open = opener or urllib.request.urlopen

    def send(self, channel: str, to: str, text: str) -> tuple[bool, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url, data=json.dumps({"channel": channel, "to": to, "text": text}).encode(),
                                     headers=headers)
        try:
            with self._open(req, timeout=15) as r:
                return 200 <= r.status < 300, f"HTTP {r.status}"
        except Exception as err:  # noqa: BLE001
            return False, str(err)[:300]


def from_env(env=None):
    """The sender the environment asks for. Anything missing or unclear falls back to dry run."""
    env = os.environ if env is None else env
    provider = env.get("PLANNER_REMINDER_PROVIDER", "").strip().lower()
    if env.get("PLANNER_REMINDERS_LIVE", "").strip() != "1":
        return DryRunSender("PLANNER_REMINDERS_LIVE is not 1")
    if provider == "twilio":
        sid, token = env.get("TWILIO_ACCOUNT_SID", ""), env.get("TWILIO_AUTH_TOKEN", "")
        if not (sid and token):
            return DryRunSender("TWILIO_ACCOUNT_SID or TWILIO_AUTH_TOKEN missing")
        return TwilioSender(sid, token, env.get("TWILIO_SMS_FROM", ""), env.get("TWILIO_WHATSAPP_FROM", ""))
    if provider == "webhook":
        url = env.get("PLANNER_REMINDER_WEBHOOK_URL", "")
        if not url:
            return DryRunSender("PLANNER_REMINDER_WEBHOOK_URL missing")
        return WebhookSender(url, env.get("PLANNER_REMINDER_WEBHOOK_TOKEN", ""))
    return DryRunSender(f"unknown provider '{provider}'" if provider else "PLANNER_REMINDER_PROVIDER not set")


def default_channel(env=None) -> str:
    env = os.environ if env is None else env
    ch = env.get("PLANNER_REMINDER_CHANNEL", "whatsapp").strip().lower()
    return ch if ch in CHANNELS else "whatsapp"
