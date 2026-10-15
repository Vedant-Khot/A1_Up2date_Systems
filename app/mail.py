"""Poll one IMAP mailbox and turn new messages into jobs."""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Optional

from app.store import BRAND, ROLE_LABELS, store

log = logging.getLogger("sitelog.mail")
IMAP_TIMEOUT_SECONDS = 10
OUTBOX: list[dict[str, Any]] = []


def _header(value: str) -> str:
    parts = []
    for text, encoding in decode_header(value or ""):
        if isinstance(text, bytes):
            parts.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


def _body(message: email.message.Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition") or ""):
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip()
        return ""
    payload = message.get_payload(decode=True) or b""
    if isinstance(payload, str):
        return payload.strip()
    return payload.decode(message.get_content_charset() or "utf-8", errors="replace").strip()


def readable_mail_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "The mailbox did not respond in time."
    if isinstance(exc, ssl.SSLError):
        return "Could not start an SSL connection. Port 993 is the usual SSL port."
    if isinstance(exc, imaplib.IMAP4.error):
        text = str(exc).lower()
        if "auth" in text or "login" in text or "credential" in text or "invalid" in text:
            return "The mailbox rejected the email or password."
        return "The mailbox refused the connection."
    if isinstance(exc, OSError):
        return "Could not reach the IMAP host."
    return "The mailbox check failed."


def probe_mailbox(host: str, port: int, email_address: str, password: str) -> str:
    """Return an empty string when IMAP login works, otherwise a short error."""
    host = host.strip()
    email_address = email_address.strip()
    if not host or not email_address or not password:
        return "Host, email, and password are required."
    if port < 1 or port > 65535:
        return "Port must be between 1 and 65535. 993 is the usual SSL port."
    client = None
    try:
        client = imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT_SECONDS)
        client.login(email_address, password)
        return ""
    except Exception as exc:
        return readable_mail_error(exc)
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass


def poll_once() -> int:
    mailbox = store.mailbox_secret()
    if not mailbox or not mailbox["host"] or not mailbox["email"] or not mailbox["password"]:
        return 0
    created = 0
    error = ""
    client = None
    try:
        client = imaplib.IMAP4_SSL(mailbox["host"], int(mailbox["port"] or 993), timeout=IMAP_TIMEOUT_SECONDS)
        client.login(mailbox["email"], mailbox["password"])
        client.select("INBOX")
        status, data = client.search(None, "UNSEEN")
        if status != "OK" or not data or data[0] is None:
            error = "Could not read the inbox."
            return 0
        for number in data[0].split():
            fetched, payload = client.fetch(number, "(RFC822)")
            if fetched != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            message = email.message_from_bytes(payload[0][1])
            message_id = (message.get("Message-ID") or f"imap-{number.decode()}").strip()
            if store.email_seen(message_id):
                continue
            sender = parseaddr(_header(message.get("From") or ""))[1] or mailbox["email"]
            subject = _header(message.get("Subject") or "(no subject)")
            body = _body(message)[:8000]
            job = store.save_job(
                {
                    "work_ref": subject[:80] or "Email job",
                    "client": sender,
                    "site_address": "From email",
                    "issue": body[:500],
                    "email_from": sender,
                    "email_subject": subject,
                    "email_body": body,
                    "email_message_id": message_id,
                },
                None,
                source="email",
            )
            store.remember_email(message_id, job["id"])
            client.store(number, "+FLAGS", "\\Seen")
            created += 1
        return created
    except Exception as exc:
        error = readable_mail_error(exc)
        raise
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass
        store.record_mailbox_poll(created, error)


async def poll_loop() -> None:
    while True:
        try:
            count = await asyncio.to_thread(poll_once)
            if count:
                log.info("Saved %s jobs from email", count)
        except Exception as exc:
            log.warning("Mailbox poll failed: %s", exc)
        await asyncio.sleep(60)


def _smtp_settings() -> Optional[dict]:
    host = os.environ.get("SITELOG_SMTP_HOST", "").strip()
    user = os.environ.get("SITELOG_SMTP_USER", "").strip()
    password = os.environ.get("SITELOG_SMTP_PASSWORD", "").strip()
    sender = os.environ.get("SITELOG_SMTP_FROM", "").strip() or user
    try:
        port = int(os.environ.get("SITELOG_SMTP_PORT", "587") or "587")
    except ValueError:
        port = 587
    if host and user and password and sender:
        return {"host": host, "port": port, "user": user, "password": password, "sender": sender}
    try:
        mailbox = store.mailbox_secret()
    except Exception:
        mailbox = None
    if not mailbox or not mailbox.get("host") or not mailbox.get("email") or not mailbox.get("password"):
        return None
    imap_host = str(mailbox["host"]).strip()
    smtp_host = imap_host.replace("imap.", "smtp.", 1) if imap_host.lower().startswith("imap.") else imap_host
    return {
        "host": smtp_host,
        "port": 587,
        "user": mailbox["email"],
        "password": mailbox["password"],
        "sender": mailbox["email"],
    }


def send_login_email(
    *,
    to_name: str,
    to_email: str,
    password: str,
    login_url: str,
    phone: str = "",
    role: str = "engineer",
    engineer_id: str = "",
) -> str:
    """Email the new engineer their complete profile and sign-in details.

    Includes Full Name, Role, Email, Mobile Phone, Temporary Password, Account ID,
    Registration Date, and Portal Login link in both plain-text and rich HTML format.
    Empty string return means the message was sent or recorded successfully.
    """
    to_name = to_name.strip()
    to_email = to_email.strip()
    phone = phone.strip()
    created_at = datetime.now().strftime("%d %b %Y, %H:%M")
    role_label = ROLE_LABELS.get(role, "Field Service Engineer")
    if role == "engineer":
        role_label = "Field Service Engineer"

    text_content = (
        f"Hello {to_name},\n\n"
        f"Welcome to {BRAND}! Your Field Service Engineer account has been successfully created.\n\n"
        "Here are all your registration and profile details:\n"
        "--------------------------------------------------\n"
        f"Full Name:           {to_name}\n"
        f"Assigned Role:       {role_label}\n"
        f"Work Email:          {to_email}\n"
        f"Mobile Phone:        {phone or 'Not specified'}\n"
        f"Temporary Password:  {password}\n"
        f"Account Ref ID:      {engineer_id or 'Auto-generated'}\n"
        f"Registration Date:   {created_at}\n"
        "--------------------------------------------------\n\n"
        "Portal Sign-In:\n"
        f"Login URL: {login_url}\n"
        f"Username:  {to_email}\n"
        f"Password:  {password}\n\n"
        "Next Steps:\n"
        "1. Open the portal URL above in your browser.\n"
        "2. Log in using your work email and temporary password.\n"
        "3. Review dispatched site visits, track your hours, attach completion photos, and chat with office coordinators.\n\n"
        "Security Notice:\n"
        "Please keep these login credentials confidential. If you require any assistance, reach out to your office operations coordinator.\n\n"
        f"Best regards,\n"
        f"{BRAND} Operations & Contractor Reporting System\n"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your {BRAND} Account Details</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #111827;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f3f4f6; padding: 32px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 580px; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 14px rgba(0,0,0,0.06); border: 1px solid #e5e7eb;">
          <tr>
            <td style="height: 6px; background: linear-gradient(90deg, #9333ea 0%, #6366f1 100%);"></td>
          </tr>
          <tr>
            <td style="background-color: #030712; padding: 24px 32px;">
              <table role="presentation" width="100%">
                <tr>
                  <td>
                    <span style="font-size: 20px; font-weight: 900; letter-spacing: -0.5px; color: #ffffff;">{BRAND}</span>
                    <span style="display: inline-block; margin-left: 8px; font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; background: #9333ea; color: #ffffff; padding: 2px 8px; border-radius: 4px;">Staff Welcome</span>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding: 32px;">
              <h1 style="margin: 0 0 8px 0; font-size: 22px; font-weight: 800; color: #111827;">Welcome to the Team, {to_name}!</h1>
              <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.5; color: #4b5563;">
                Your field engineer account has been created on the <strong>{BRAND}</strong> contractor reporting system. Below are all your registration details and sign-in credentials:
              </p>

              <div style="background-color: #f9fafb; border: 1px solid #e5e7eb; border-radius: 12px; padding: 20px; margin-bottom: 24px;">
                <table role="presentation" width="100%" style="font-size: 13px; line-height: 1.6;">
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600; width: 38%;">Full Name:</td>
                    <td style="padding: 6px 0; color: #111827; font-weight: 700;">{to_name}</td>
                  </tr>
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600;">Assigned Role:</td>
                    <td style="padding: 6px 0;">
                      <span style="display: inline-block; background-color: #f3e8ff; color: #7e22ce; font-weight: 700; font-size: 11px; padding: 2px 8px; border-radius: 6px; text-transform: uppercase;">{role_label}</span>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600;">Work Email:</td>
                    <td style="padding: 6px 0; color: #111827; font-weight: 600;">{to_email}</td>
                  </tr>
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600;">Mobile Phone:</td>
                    <td style="padding: 6px 0; color: #111827; font-family: monospace; font-weight: 600;">{phone or 'Not specified'}</td>
                  </tr>
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600;">Temporary Password:</td>
                    <td style="padding: 6px 0;">
                      <code style="background-color: #f3f4f6; border: 1px solid #d1d5db; color: #9333ea; font-weight: 700; font-size: 13px; padding: 3px 8px; border-radius: 6px; font-family: monospace;">{password}</code>
                    </td>
                  </tr>
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600;">Account Ref ID:</td>
                    <td style="padding: 6px 0; color: #6b7280; font-family: monospace; font-size: 11px;">{engineer_id or 'Auto-generated'}</td>
                  </tr>
                  <tr>
                    <td style="padding: 6px 0; color: #6b7280; font-weight: 600;">Registration Date:</td>
                    <td style="padding: 6px 0; color: #6b7280; font-size: 12px;">{created_at}</td>
                  </tr>
                </table>
              </div>

              <table role="presentation" width="100%" style="margin-bottom: 24px;">
                <tr>
                  <td align="center">
                    <a href="{login_url}" style="display: inline-block; background-color: #9333ea; color: #ffffff; text-decoration: none; font-size: 14px; font-weight: 700; padding: 12px 28px; border-radius: 10px; box-shadow: 0 2px 4px rgba(147, 51, 234, 0.3);">
                      Sign in to Engineer Portal &rarr;
                    </a>
                  </td>
                </tr>
              </table>

              <div style="background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 12px 16px; border-radius: 8px; margin-bottom: 24px;">
                <p style="margin: 0; font-size: 12px; color: #1e40af; line-height: 1.4;">
                  <strong>Quick Tip:</strong> After signing in, you will be able to access your dispatched jobs, submit site survey reports, upload photos, and chat directly with office coordinators.
                </p>
              </div>

              <p style="margin: 0; font-size: 13px; color: #6b7280; line-height: 1.5;">
                Please keep your password confidential. If you did not expect this invitation or need assistance, contact your office operations coordinator.
              </p>
            </td>
          </tr>
          <tr>
            <td style="background-color: #f9fafb; border-top: 1px solid #e5e7eb; padding: 20px 32px; text-align: center;">
              <p style="margin: 0 0 4px 0; font-size: 11px; font-weight: 700; color: #374151;">{BRAND} Systems</p>
              <p style="margin: 0; font-size: 10px; color: #9ca3af;">Operations & Field Contractor Reporting Platform • Automated Dispatch</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    message = EmailMessage()
    message["Subject"] = f"Welcome to {BRAND} - Your Field Engineer Account Details"
    settings = _smtp_settings()
    message["From"] = str(settings["sender"]) if settings else f"noreply@{BRAND.lower()}.demo"
    message["To"] = to_email
    message.set_content(text_content)
    message.add_alternative(html_content, subtype="html")

    outbox_entry = {
        "to_name": to_name,
        "to_email": to_email,
        "phone": phone,
        "role": role_label,
        "password": password,
        "login_url": login_url,
        "engineer_id": engineer_id,
        "created_at": created_at,
        "subject": message["Subject"],
        "text": text_content,
        "html": html_content,
    }

    if os.environ.get("SITELOG_TEST_MODE") == "1":
        OUTBOX.append(outbox_entry)
        log.info("Test mode: recorded welcome email for %s", to_email)
        return ""

    if settings is None:
        OUTBOX.append(outbox_entry)
        log.info("Demo mode (no SMTP configured): recorded welcome email for %s", to_email)
        return ""

    host = str(settings["host"])
    port = int(settings["port"])
    user = str(settings["user"])
    secret = str(settings["password"])

    if host.endswith("example.com") or to_email.endswith(".demo"):
        OUTBOX.append(outbox_entry)
        log.info("Demo host (%s): simulated welcome email recorded for %s", host, to_email)
        return ""

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context()) as client:
                client.login(user, secret)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=10) as client:
                client.ehlo()
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
                client.login(user, secret)
                client.send_message(message)
        OUTBOX.append(outbox_entry)
        log.info("Successfully sent welcome email to %s via %s", to_email, host)
        return ""
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("Could not email a login: %s", exc)
        OUTBOX.append(outbox_entry)
        return f"Could not connect to SMTP server ({host}:{port}): {exc}"

