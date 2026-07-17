import logging

import requests
from django.conf import settings

from .base import ChannelError

logger = logging.getLogger(__name__)


class StubWhatsAppAdapter:
    """Development stand-in: logs the message instead of calling Meta.

    The Notification row itself (visible in the admin) is the delivery record.
    """

    def send(self, notification):
        logger.info(
            "[stub WhatsApp] to=%s template=%s params=%s",
            notification.recipient_phone,
            notification.wa_template_name,
            notification.wa_params,
        )
        return "stub"


class WhatsAppCloudAdapter:
    """Meta WhatsApp Cloud API: business-initiated template messages only.

    The template named on the notification must be pre-approved in Meta
    Business Manager for the configured WhatsApp Business phone number.
    """

    def send(self, notification):
        if not notification.recipient_phone:
            raise ChannelError("No phone number for recipient", permanent=True)
        if not notification.wa_template_name:
            raise ChannelError("No WhatsApp template configured", permanent=True)

        url = (
            f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/"
            f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
        )
        payload = {
            "messaging_product": "whatsapp",
            "to": notification.recipient_phone.lstrip("+"),
            "type": "template",
            "template": {
                "name": notification.wa_template_name,
                "language": {"code": notification.wa_language or "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": param}
                            for param in notification.wa_params
                        ],
                    }
                ]
                if notification.wa_params
                else [],
            },
        }
        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ChannelError(f"Network error: {exc}") from exc

        if response.status_code == 200:
            data = response.json()
            try:
                return data["messages"][0]["id"]
            except (KeyError, IndexError):
                return ""

        # 4xx (other than throttling) = our payload/number/template is wrong:
        # retrying the same message cannot succeed.
        detail = response.text[:500]
        if response.status_code == 429 or response.status_code >= 500:
            raise ChannelError(f"HTTP {response.status_code}: {detail}")
        raise ChannelError(f"HTTP {response.status_code}: {detail}", permanent=True)
