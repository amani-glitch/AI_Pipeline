"""
Email notification service — send deployment status emails via Gmail API.

Uses a GCP service account with domain-wide delegation to send emails
through the Gmail API.  Gracefully degrades when the service account is
not configured (logs a warning instead of crashing).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from jinja2 import Template

from config import Settings

logger = logging.getLogger("webdeploy.email_service")

# Gmail API scope required for sending emails
_GMAIL_SEND_SCOPE = ["https://www.googleapis.com/auth/gmail.send"]

# ── HTML email template ──────────────────────────────────────────────
_EMAIL_TEMPLATE = Template("""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WebDeploy Notification</title>
<style>
  body { margin: 0; padding: 0; background-color: #f4f6f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }
  .container { max-width: 600px; margin: 40px auto; background: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }
  .header { padding: 24px 32px; color: #ffffff; }
  .header-success { background: linear-gradient(135deg, #10b981, #059669); }
  .header-failed { background: linear-gradient(135deg, #ef4444, #dc2626); }
  .header h1 { margin: 0; font-size: 20px; font-weight: 600; }
  .header p { margin: 6px 0 0; font-size: 13px; opacity: 0.85; }
  .body { padding: 28px 32px; color: #1f2937; }
  .field { margin-bottom: 16px; }
  .field-label { font-size: 12px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
  .field-value { font-size: 15px; color: #111827; }
  .field-value a { color: #2563eb; text-decoration: none; }
  .field-value a:hover { text-decoration: underline; }
  .summary-box { background: #f9fafb; border-left: 4px solid #6366f1; padding: 14px 18px; margin: 20px 0; border-radius: 0 6px 6px 0; font-size: 14px; line-height: 1.6; color: #374151; }
  .error-box { background: #fef2f2; border-left: 4px solid #ef4444; padding: 14px 18px; margin: 20px 0; border-radius: 0 6px 6px 0; font-size: 14px; line-height: 1.6; color: #991b1b; font-family: monospace; white-space: pre-wrap; word-break: break-word; }
  .footer { padding: 16px 32px; background: #f9fafb; text-align: center; font-size: 12px; color: #9ca3af; border-top: 1px solid #e5e7eb; }
</style>
</head>
<body>
<div class="container">
  <div class="header {{ 'header-success' if success else 'header-failed' }}">
    <h1>{{ '&#9989;' if success else '&#10060;' }} {{ website_name }} &mdash; Deployment {{ 'Succeeded' if success else 'Failed' }}</h1>
    <p>{{ mode | upper }} mode &bull; {{ timestamp }}</p>
  </div>

  <div class="body">
    <div class="field">
      <div class="field-label">Website</div>
      <div class="field-value">{{ website_name }}</div>
    </div>

    <div class="field">
      <div class="field-label">Mode</div>
      <div class="field-value">{{ mode | upper }}</div>
    </div>

    <div class="field">
      <div class="field-label">Status</div>
      <div class="field-value">{{ 'SUCCESS' if success else 'FAILED' }}</div>
    </div>

    {% if success and live_url %}
    <div class="field">
      <div class="field-label">Live URL</div>
      <div class="field-value"><a href="{{ live_url }}">{{ live_url }}</a></div>
    </div>
    {% endif %}

    {% if claude_summary %}
    <div class="field">
      <div class="field-label">Claude AI Summary</div>
      <div class="summary-box">{{ claude_summary }}</div>
    </div>
    {% endif %}

    {% if not success and error_message %}
    <div class="field">
      <div class="field-label">Error Details</div>
      <div class="error-box">{{ error_message }}</div>
    </div>
    {% endif %}
  </div>

  <div class="footer">
    Sent by <strong>WebDeploy</strong> &bull; {{ timestamp }}
  </div>
</div>
</body>
</html>
""")


class EmailService:
    """Send deployment notification emails via the Gmail API (service account)."""

    def __init__(
        self,
        log_callback: Optional[Callable] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._log = log_callback or (lambda msg, **kw: None)
        self._settings = settings or Settings()

    # ── Public API ────────────────────────────────────────────────────

    async def send_notification(
        self,
        website_name: str,
        mode: str,
        success: bool,
        live_url: Optional[str] = None,
        error_message: Optional[str] = None,
        claude_summary: Optional[str] = None,
        recipients: Optional[list[str]] = None,
    ) -> bool:
        """
        Send a deployment notification email.

        Parameters
        ----------
        website_name : str
            Name of the deployed website.
        mode : str
            "demo" or "prod".
        success : bool
            Whether the deployment succeeded.
        live_url : str, optional
            The URL where the site is accessible (on success).
        error_message : str, optional
            Error details (on failure).
        claude_summary : str, optional
            AI inspection summary.
        recipients : list[str], optional
            Override recipient list. Falls back to settings.

        Returns
        -------
        bool
            True if the email was sent successfully, False otherwise.
        """
        to_emails = recipients or self._settings.notification_emails_list
        if not to_emails:
            self._log(
                "No notification recipients configured — skipping email",
                level="WARNING",
                step="NOTIFY",
            )
            return False

        if not self._is_gmail_configured():
            self._log(
                "Gmail API not configured (missing service account or delegated user) — skipping email",
                level="WARNING",
                step="NOTIFY",
            )
            return False

        self._log(
            f"Sending notification email to {len(to_emails)} recipient(s) via Gmail API",
            level="INFO",
            step="NOTIFY",
        )

        try:
            msg = self._build_message(
                website_name=website_name,
                mode=mode,
                success=success,
                live_url=live_url,
                error_message=error_message,
                claude_summary=claude_summary,
                to_emails=to_emails,
            )

            # Gmail API calls are synchronous — run in a thread pool
            await asyncio.to_thread(self._send_via_gmail, msg)

            self._log("Notification email sent successfully via Gmail API", level="INFO", step="NOTIFY")
            return True

        except Exception as exc:
            self._log(
                f"Error sending notification email via Gmail API: {exc}",
                level="ERROR",
                step="NOTIFY",
            )
            logger.exception("Gmail API sending failed")
            return False

    # ── Private helpers ───────────────────────────────────────────────

    def _is_gmail_configured(self) -> bool:
        """Check whether Gmail API credentials are available."""
        import os
        has_creds = (
            self._settings.GOOGLE_APPLICATION_CREDENTIALS
            and os.path.isfile(self._settings.GOOGLE_APPLICATION_CREDENTIALS)
        )
        # Also consider ADC available on Cloud Run
        if not has_creds:
            try:
                import google.auth
                google.auth.default(scopes=_GMAIL_SEND_SCOPE)
                has_creds = True
            except Exception:
                has_creds = False
        return bool(has_creds and self._settings.GMAIL_DELEGATED_USER)

    def _get_gmail_service(self):
        """Build an authorised Gmail API service using the service account."""
        import os
        creds_path = self._settings.GOOGLE_APPLICATION_CREDENTIALS
        if creds_path and os.path.isfile(creds_path):
            credentials = service_account.Credentials.from_service_account_file(
                creds_path,
                scopes=_GMAIL_SEND_SCOPE,
            )
        else:
            # On Cloud Run, try to load the service account key from
            # Secret Manager or a well-known path inside the container.
            _alt_paths = [
                "/app/secrets/service-account.json",
                "/secrets/service-account.json",
                "/app/service-account.json",
            ]
            sa_path = None
            for p in _alt_paths:
                if os.path.isfile(p):
                    sa_path = p
                    break

            if sa_path:
                credentials = service_account.Credentials.from_service_account_file(
                    sa_path,
                    scopes=_GMAIL_SEND_SCOPE,
                )
            else:
                import google.auth
                credentials, _ = google.auth.default(scopes=_GMAIL_SEND_SCOPE)

        # Delegate to the actual sender mailbox
        # with_subject() is only available on service_account.Credentials.
        # ADC (compute engine) credentials do NOT support domain-wide delegation.
        if not hasattr(credentials, "with_subject"):
            raise RuntimeError(
                "Gmail domain-wide delegation requires a service-account key file. "
                "ADC / compute-engine credentials do not support with_subject(). "
                "Mount the service-account JSON via Secret Manager or copy it into the container."
            )
        delegated = credentials.with_subject(self._settings.GMAIL_DELEGATED_USER)
        delegated.refresh(Request())
        return build("gmail", "v1", credentials=delegated, cache_discovery=False)

    def _send_via_gmail(self, msg: MIMEMultipart) -> None:
        """Send a MIME message through the Gmail API (runs in thread)."""
        service = self._get_gmail_service()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

    def _build_message(
        self,
        website_name: str,
        mode: str,
        success: bool,
        live_url: Optional[str],
        error_message: Optional[str],
        claude_summary: Optional[str],
        to_emails: list[str],
    ) -> MIMEMultipart:
        """Build the MIME email message with HTML body."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Subject line
        if success:
            subject = f"[WebDeploy] \u2705 {website_name} deployed successfully \u2014 {mode} mode"
        else:
            subject = f"[WebDeploy] \u274c {website_name} deployment failed \u2014 {mode} mode"

        # Render HTML body
        html_body = _EMAIL_TEMPLATE.render(
            website_name=website_name,
            mode=mode,
            success=success,
            live_url=live_url,
            error_message=error_message,
            claude_summary=claude_summary,
            timestamp=timestamp,
        )

        # Build MIME message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._settings.NOTIFICATION_FROM_EMAIL
        msg["To"] = ", ".join(to_emails)

        # Plain-text fallback
        if success:
            plain = (
                f"WebDeploy Notification\n\n"
                f"Website: {website_name}\n"
                f"Mode: {mode.upper()}\n"
                f"Status: SUCCESS\n"
                f"URL: {live_url or 'N/A'}\n"
            )
        else:
            plain = (
                f"WebDeploy Notification\n\n"
                f"Website: {website_name}\n"
                f"Mode: {mode.upper()}\n"
                f"Status: FAILED\n"
                f"Error: {error_message or 'Unknown error'}\n"
            )

        if claude_summary:
            plain += f"\nClaude AI Summary:\n{claude_summary}\n"

        plain += f"\nTimestamp: {timestamp}\n"

        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        return msg
