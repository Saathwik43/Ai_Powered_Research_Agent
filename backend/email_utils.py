import os
import logging
import httpx

logger = logging.getLogger(__name__)

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL")
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "AI Research Agent")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


class EmailSendError(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


async def send_reset_email(to_email: str, token: str) -> None:
    if not BREVO_API_KEY or not BREVO_SENDER_EMAIL:
        logger.error("Brevo config missing: BREVO_API_KEY/BREVO_SENDER_EMAIL not set.")
        raise EmailSendError("config_missing", "BREVO_API_KEY or BREVO_SENDER_EMAIL not set in env")

    link = f"{FRONTEND_URL}/reset-password?token={token}"
    payload = {
        "sender": {"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": "Password Reset Request",
        "textContent": f"Reset your password: {link}\nExpires in 30 minutes.",
    }
    headers = {"api-key": BREVO_API_KEY, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(BREVO_URL, json=payload, headers=headers)
        resp.raise_for_status()
        logger.info(f"Password reset email sent to {to_email} (Brevo messageId={resp.json().get('messageId')})")

    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        logger.error(f"Brevo rejected email to {to_email}: {e.response.status_code} {body}")
        raise EmailSendError("api_error", f"{e.response.status_code}: {body}") from e

    except httpx.RequestError as e:
        logger.error(f"Network error sending email to {to_email}: {e}")
        raise EmailSendError("network_error", str(e)) from e

    except Exception as e:
        logger.error(f"Unexpected error sending email to {to_email}: {e}")
        raise EmailSendError("unknown_error", str(e)) from e