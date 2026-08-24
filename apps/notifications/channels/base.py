class ChannelError(Exception):
    """Delivery failure. `permanent=True` means retrying cannot help
    (invalid address, rejected template) and the notification fails fast."""

    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


def get_adapter(channel, **kwargs):
    """Resolve the adapter configured for a channel in settings.NOTIFICATION_CHANNELS.

    Keyword arguments are passed to the adapter's constructor. The worker uses
    this to hand the email adapter one already-open SMTP connection for a whole
    batch; see apps.notifications.worker.delivery_session.
    """
    from django.conf import settings
    from django.utils.module_loading import import_string

    path = settings.NOTIFICATION_CHANNELS[channel]
    return import_string(path)(**kwargs)
