import logging
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageWarning:
    email: str
    used: int
    limit: int
    period_end: datetime


@dataclass(frozen=True, slots=True)
class UsageLimitReached:
    email: str
    plan: str
    used: int
    limit: int
    period_end: datetime


class EmailProvider:
    def send_usage_warning(self, warning: UsageWarning) -> None:
        raise NotImplementedError

    def send_usage_limit_reached(self, notice: UsageLimitReached) -> None:
        raise NotImplementedError


class ResendEmailProvider(EmailProvider):
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.resend_api_key
        self.sender = settings.email_from
        self.upgrade_url = f"{settings.website_url.rstrip('/')}/pricing/"

    def send_usage_warning(self, warning: UsageWarning) -> None:
        if not self.api_key:
            return
        remaining = max(0, warning.limit - warning.used)
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "from": self.sender,
                "to": [warning.email],
                "subject": "You have used 90% of your BarcodeNest API allowance",
                "html": (
                    f"<p>You have used <strong>{warning.used}</strong> of {warning.limit} API calls.</p>"
                    f"<p>{remaining} calls remain until {warning.period_end.date().isoformat()}.</p>"
                    f'<p><a href="{self.upgrade_url}">View upgrade options</a></p>'
                ),
            },
            timeout=10,
        )
        response.raise_for_status()

    def send_usage_limit_reached(self, notice: UsageLimitReached) -> None:
        if not self.api_key:
            return
        response = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "from": self.sender,
                "to": [notice.email],
                "subject": "You have reached your BarcodeNest API limit",
                "html": (
                    "<h1>Your monthly API allowance has been used</h1>"
                    f"<p>Your <strong>{notice.plan.title()}</strong> plan has used "
                    f"<strong>{notice.used}</strong> of {notice.limit} API calls.</p>"
                    f"<p>Your allowance resets on {notice.period_end.date().isoformat()}. "
                    "Until then, additional product lookups will be blocked.</p>"
                    f'<p><a href="{self.upgrade_url}">Upgrade your BarcodeNest plan</a> '
                    "to restore access with a higher monthly allowance.</p>"
                    "<p>— The BarcodeNest team</p>"
                ),
            },
            timeout=10,
        )
        response.raise_for_status()


def send_usage_warning_safely(settings: Settings, warning: UsageWarning | None) -> None:
    if warning is None:
        return
    try:
        ResendEmailProvider(settings).send_usage_warning(warning)
    except Exception as exc:
        logger.warning("Usage warning email delivery failed: %s", type(exc).__name__)


def send_usage_limit_reached_safely(
    settings: Settings, notice: UsageLimitReached | None
) -> None:
    if notice is None:
        return
    try:
        ResendEmailProvider(settings).send_usage_limit_reached(notice)
    except Exception as exc:
        logger.warning("Usage limit email delivery failed: %s", type(exc).__name__)
