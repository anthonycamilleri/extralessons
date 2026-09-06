# Changing a class and its dates

A class has two separate things you can change: **what it says**, and **when it
meets**. They behave differently, and mixing them up is the easiest mistake to
make here.

## Where to find it {#where}

Click **Classes** at the top of any admin page for this term's classes, then
click a title to open it.

![The class list, with columns for registrations, confirmed, available, waiting and pending](class-list.png)

The columns are the term at a glance: how many families have registered, how
many places are confirmed, what parents are shown as still available, how many
are waiting, and how many requests are still yours to review.

## Changing the details {#details}

Everything on the class form — title, description, extra details, cover image,
location, ages, capacity — is safe to change at any time. Parents see the new
wording straight away.

![The class edit form](class-form.png)

Two details are worth knowing:

- **Ages are a recommendation, not a rule.** A parent registering a child
  outside the range is warned and can carry on; the request is flagged for you
  on the Requests page.
- **Capacity cannot go below the seats already taken.** If you need a smaller
  class, cancel some places first. Raising the capacity, on the other hand,
  alerts you if children are waiting — see
  [Waiting lists and offers](waiting-lists).

You cannot change **Status** on the form. Publishing, cancelling and archiving
are done from the **Action** menu on the class list, so that families always
get told.

## How the lesson dates are worked out {#dates}

You never type the dates in. The app works them out from three things:

1. the **term's** start and end dates,
2. the class's **weekday**,
3. the **school holidays** on that school year.

Every matching date becomes a lesson, and holidays are skipped.

![The Class sessions rows on the class form](class-sessions.png)

> **Editing the class does not move the lessons.**
>
> If you change the weekday, the times or the term, the lessons already listed
> stay exactly where they were. Nothing warns you. Afterwards, run
> **Regenerate sessions** — see below.

## Regenerating {#regenerate}

On the class list, tick the class and choose **Regenerate sessions (skips
school holidays)** from the **Action** menu.

![The Action menu on the class list, open on Regenerate sessions](class-actions.png)

It rebuilds the dates from the three things above and tells you what it did:
*Reconciled 1 class: 12 sessions created, 2 removed, 1 date skipped for school
holidays.*

It is safe to run as often as you like. It never touches:

- lessons in the past,
- lessons where attendance has already been taken,
- lessons you have marked as cancelled.

It **does** delete future lessons that no longer fit — which is the point, but
also means a one-off date you added by hand on a different weekday will
disappear.

## Cancelling one lesson {#cancel-one}

Do not delete the row: regenerating puts it back. Instead, open the class,
find the date in **Class sessions**, and tick **cancelled**. Add a note if it
helps. Parents see the lesson marked as cancelled on their family page.

## A lesson during a holiday {#holidays}

Holidays live on the **school year**, once, and every term in that year
inherits them. That is why you only enter them one time.

Two ways to run anyway:

| You want | Where | Tick |
|---|---|---|
| One date to go ahead during a break | that row in **Class sessions** | **runs despite holiday** |
| The whole class to ignore holidays (a holiday camp) | the class form | **Runs during holidays** |

If you forget the first one, the next regeneration removes that date again.

Editing the holidays themselves updates every class in that year that already
has lessons — so adding a half-term after classes are published does close
those dates. It only closes future dates; history is left alone.

## The one to be careful with {#terms}

> **Do not move a term's dates to shift one class.**
>
> A term is shared. Every class in it follows the new dates the next time its
> lessons are regenerated. If one class needs to start later or finish earlier,
> leave the term alone and cancel the lessons at either end instead.

And if you do change a term's dates on purpose, remember that nothing happens
by itself: regenerate the classes in that term afterwards.
