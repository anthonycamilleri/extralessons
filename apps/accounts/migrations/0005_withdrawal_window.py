"""The withdrawal window, and the terms paragraph that explains it.

Only the "Cancellations and withdrawals" section of the terms is touched, and
only where it still reads exactly as seeded (0002): an office that has
reworded its terms keeps every word.
"""
from django.db import migrations, models

OLD_SECTION = (
    "## Cancellations and withdrawals\n\n"
    "Withdrawals are processed through the PTA website, not directly with the "
    "provider. This keeps waiting lists and class numbers accurate for everyone.\n"
)
NEW_SECTION = OLD_SECTION + (
    "\nDuring the first two weeks after registering you can withdraw your child "
    "from a class yourself, from your family page, with immediate effect — the "
    "place goes straight back to the waiting list. A request that hasn't been "
    "confirmed yet, a waiting-list entry or a seat offer can be withdrawn at any "
    "time.\n\n"
    "After those two weeks, a confirmed place can only be cancelled by asking the "
    "PTA. Use the Cancel button on your family page: we'll confirm the "
    "cancellation by email, and until we do the place is still your child's, so "
    "please keep attending and note that the provider's fees may still be due for "
    "the current period. Providers plan staff and space around the children "
    "enrolled, which is why we ask you to decide within the first two weeks where "
    "you can.\n"
)


def _swap(apps, source, target):
    SiteConfig = apps.get_model("accounts", "SiteConfig")
    for config in SiteConfig.objects.all():
        if source in config.terms_markdown and target not in config.terms_markdown:
            config.terms_markdown = config.terms_markdown.replace(source, target, 1)
            config.save(update_fields=["terms_markdown"])


def forwards(apps, schema_editor):
    _swap(apps, OLD_SECTION, NEW_SECTION)


def backwards(apps, schema_editor):
    _swap(apps, NEW_SECTION, OLD_SECTION)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_admins_are_staff"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="withdrawal_window_days",
            field=models.PositiveSmallIntegerField(
                default=14,
                help_text="Days after registering during which a family can withdraw a confirmed place themselves, with immediate effect. After that they can only ask to cancel, and an admin confirms (or keeps the place). Requests, waiting-list entries and offers can always be withdrawn.",
            ),
        ),
        migrations.AlterField(
            model_name="siteconfig",
            name="terms_markdown",
            field=models.TextField(
                blank=True,
                default="# Extra-Curricular Activities — Terms & Conditions\n\nThe extra-curricular programme is organised by the PTA on a fully voluntary basis. Parent volunteers find providers, negotiate rates and conditions, and run enrolment. Nobody is paid for this.\n\nPlease read these terms before enrolling — they exist to keep classes viable and the programme sustainable.\n\n## Our role — and who you're actually contracting with\n\nThe PTA negotiates with providers on behalf of parents and coordinates enrolment. We take provider selection seriously: we've put considerable effort into vetting everyone on this year's programme. All providers work or have worked with other schools, and we ask for and check the certifications and qualifications appropriate to their activity.\n\nThat said, once your child has a place, the agreement is directly between you and the provider. The PTA is not a party to it, does not employ providers, and cannot take final responsibility for the delivery, quality or safety of an activity. We've done our best to choose well, but we're parent volunteers, not a licensing authority.\n\nConcerns about a class are best raised with the provider first. If something isn't resolved, tell us — we'll take it up with them and in the next round of negotiations.\n\n## Enrolment\n\nAll registrations must be made through the web form on the PTA website. We can't accept enrolments by email, message, in person, or via the provider — if it isn't on the form, the place doesn't exist.\n\n## Payments\n\nFees are paid directly to the provider, monthly or per semester, on the terms agreed with them. The PTA does not collect or handle any money.\n\n## Cancellations and withdrawals\n\nWithdrawals are processed through the PTA website, not directly with the provider. This keeps waiting lists and class numbers accurate for everyone.\n\nDuring the first two weeks after registering you can withdraw your child from a class yourself, from your family page, with immediate effect — the place goes straight back to the waiting list. A request that hasn't been confirmed yet, a waiting-list entry or a seat offer can be withdrawn at any time.\n\nAfter those two weeks, a confirmed place can only be cancelled by asking the PTA. Use the Cancel button on your family page: we'll confirm the cancellation by email, and until we do the place is still your child's, so please keep attending and note that the provider's fees may still be due for the current period. Providers plan staff and space around the children enrolled, which is why we ask you to decide within the first two weeks where you can.\n\n## Absences and refunds\n\nNo refunds are given for absences, except for a justified absence of more than three weeks. Providers commit staff and space for the full semester, and every class needs a critical mass of children to run at all.\n\nYou don't need to notify providers of individual absences — the teacher tells the provider at pick-up. The exception is individual music lessons, where the provider should be informed directly.\n\n## Pick-up (primary cycle)\n\nChildren in primary must be collected promptly at the end of the activity. Providers finish when the session finishes; late collection means someone is supervising your child unpaid, often while another class is waiting to start.\n\nRepeated late collection may result in a child losing their place in the activity. If your child is authorised to leave on their own, this must be stated on the registration form — providers cannot act on verbal instructions.\n\n## Feedback\n\nOnce per semester we circulate a short questionnaire. Please complete it — it's the only systematic evidence we have when renegotiating with providers, and it directly shapes what we offer next year.\n",
                help_text="Markdown. Shown at /terms/ and linked from the navigation; parents must confirm they have read it before registering. Leave empty to hide it.",
                verbose_name="terms and conditions",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
