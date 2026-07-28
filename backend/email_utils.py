import os
import smtplib
import logging
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

def send_reset_email(to_email: str, token: str):
    link = f"{FRONTEND_URL}/reset-password?token={token}"
    msg = MIMEText(f"Reset your password: {link}\nExpires in 30 minutes.")
    msg["Subject"] = "Password Reset Request"
    msg["From"] = SMTP_USER
    msg["To"] = to_email

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        logger.error("SMTP configuration is missing. Cannot send reset email.")
        return

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
            logger.info(f"Password reset email sent to {to_email}")
    except Exception as e:
        logger.error(f"Failed to send reset email to {to_email}: {e}")

    