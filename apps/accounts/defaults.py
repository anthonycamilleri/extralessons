"""Default content for admin-editable site text.

Kept out of models.py so the migration that seeds it, the model default and
the tests all read one copy. Everything here is editable afterwards under
Admin → Site configuration.
"""

SENDER_NAME = "European School PTA"

TERMS_MARKDOWN = """\
# Extra-Curricular Activities — Terms & Conditions

The extra-curricular programme is organised by the PTA on a fully voluntary basis. Parent volunteers find providers, negotiate rates and conditions, and run enrolment. Nobody is paid for this.

Please read these terms before enrolling — they exist to keep classes viable and the programme sustainable.

## Our role — and who you're actually contracting with

The PTA negotiates with providers on behalf of parents and coordinates enrolment. We take provider selection seriously: we've put considerable effort into vetting everyone on this year's programme. All providers work or have worked with other schools, and we ask for and check the certifications and qualifications appropriate to their activity.

That said, once your child has a place, the agreement is directly between you and the provider. The PTA is not a party to it, does not employ providers, and cannot take final responsibility for the delivery, quality or safety of an activity. We've done our best to choose well, but we're parent volunteers, not a licensing authority.

Concerns about a class are best raised with the provider first. If something isn't resolved, tell us — we'll take it up with them and in the next round of negotiations.

## Enrolment

All registrations must be made through the web form on the PTA website. We can't accept enrolments by email, message, in person, or via the provider — if it isn't on the form, the place doesn't exist.

## Payments

Fees are paid directly to the provider, monthly or per semester, on the terms agreed with them. The PTA does not collect or handle any money.

## Cancellations and withdrawals

Withdrawals are processed through the PTA website, not directly with the provider. This keeps waiting lists and class numbers accurate for everyone.

## Absences and refunds

No refunds are given for absences, except for a justified absence of more than three weeks. Providers commit staff and space for the full semester, and every class needs a critical mass of children to run at all.

You don't need to notify providers of individual absences — the teacher tells the provider at pick-up. The exception is individual music lessons, where the provider should be informed directly.

## Pick-up (primary cycle)

Children in primary must be collected promptly at the end of the activity. Providers finish when the session finishes; late collection means someone is supervising your child unpaid, often while another class is waiting to start.

Repeated late collection may result in a child losing their place in the activity. If your child is authorised to leave on their own, this must be stated on the registration form — providers cannot act on verbal instructions.

## Feedback

Once per semester we circulate a short questionnaire. Please complete it — it's the only systematic evidence we have when renegotiating with providers, and it directly shapes what we offer next year.
"""
