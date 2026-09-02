"""Django email backends for hosted transactional-email APIs.

Django's own `EMAIL_BACKEND` setting picks one; the notifications code never
imports these directly — it sends `EmailMessage`s and lets the backend decide
how they leave the building.
"""
