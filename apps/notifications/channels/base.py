class ChannelError(Exception):
    """Delivery failure. `permanent=True` means retrying cannot help
    (invalid address, rejected template) and the notification fails fast."""

    def __init__(self, message, permanent=False):
        super().__init__(message)
        self.permanent = permanent


def get_adapter(channel):
    """Resolve the adapter configured for a channel in settings.NOTIFICATION_CHANNELS."""
    from django.conf import settings
    from django.utils.module_loading import import_string

    path = settings.NOTIFICATION_CHANNELS[channel]
    return import_string(path)()
