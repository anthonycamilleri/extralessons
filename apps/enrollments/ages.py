"""Age arithmetic for registrations.

A class's age range is a recommendation, not a rule: a parent may register a
child outside it once they have confirmed a warning, and the school decides
when it approves the request. The same helpers therefore serve the parent's
confirmation step and the admin screens that review it.

Imports nothing from models, so both models and services can use it.
"""


def age_at(date_of_birth, on_date):
    years = on_date.year - date_of_birth.year
    if (on_date.month, on_date.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return years


def age_at_term_start(child, activity_class):
    """The child's age on the first day of the class's term."""
    return age_at(child.date_of_birth, activity_class.term.start_date)


def outside_recommended_range(activity_class, child):
    """The child's age at term start, but only when it falls outside the
    class's recommended range. `None` means "within the range"."""
    age = age_at_term_start(child, activity_class)
    if activity_class.age_min <= age <= activity_class.age_max:
        return None
    return age
