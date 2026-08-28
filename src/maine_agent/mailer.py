"""Gmail SMTP delivery (app password via env vars) with a dry-run mode."""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_report(subject, html_body, dry_run=False):
    if dry_run:
        print("=" * 80)
        print(f"DRY RUN — would send email: {subject}")
        print("=" * 80)
        print(html_body)
        return

    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("REPORT_TO_ADDRESS", sender)

    missing = [n for n, v in [("GMAIL_ADDRESS", sender), ("GMAIL_APP_PASSWORD", app_password)] if not v]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s) for sending email: {', '.join(missing)}. "
            "Set them as GitHub Actions secrets or in your local environment, or run with --dry-run."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(sender, app_password)
        server.sendmail(sender, [recipient], msg.as_string())
    log.info("Sent report to %s", recipient)
