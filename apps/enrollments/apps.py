from django.apps import AppConfig


class EnrollmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.enrollments"
    # The package, app label, tables and URL names keep the American spelling
    # they were created with; what people read does not.
    verbose_name = "Enrolments"
