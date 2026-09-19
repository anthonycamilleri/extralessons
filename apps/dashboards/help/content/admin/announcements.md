# Sending an announcement

An announcement is one email (and a WhatsApp message, for families who asked
for it) to the families of the classes you choose. Use it for a change of
room, a kit reminder, a cancelled week.

## Where to find it {#where}

There are two doors, and which one you want depends on how many classes the
message concerns.

For a message that spans classes, click **Send announcement** at the top of
any admin page, or go to **Notifications → Announcements → Add announcement**.
That is the composer the rest of this page describes.

For a message to **one** class, use the **Announce** link on that class's row
in the class list — or **Send announcement** on the class's own page or its
roster. The class is already the address, so there is nothing to tick, and
the class list stays the place you work from. It is also the only way to
write to a class the composer does not list: see
[Writing to one class](#one-class).

![The announcement composer, with the classes, who gets it, subject and message](announcement-form.png)

## Filling it in {#form}

**Classes** — two choices:

- **All published classes** — every class you look after that is published
  this term. (If you are a super admin, this really does mean all of them.)
- **Selected classes** — tick the ones you mean. Only classes in the active
  term are listed.

**Who gets it** — three choices, applied inside the classes you just picked:

- **Everyone with a live place** — the usual one, and what every announcement
  did before this choice existed. "Live" is wider than you might think; see
  [Who actually gets it](#recipients) below.
- **Waiting list only** — just the families whose child is
  [on the waiting list](waiting-lists) right now. Use it to tell them where
  they stand, to ask whether they still want the place, or to say the term has
  filled up.
- **Everyone who ever had a place** — the same families plus the ones whose
  place has since been cancelled: withdrawn, not approved, or cancelled by the
  school. Use it when the news concerns people who are no longer on the list —
  above all when the class itself was cancelled, which cancelled every place in
  it and left nobody "live" to write to.

**Subject** is the email subject line. **Message** is the body, written as
you would write it to a parent. Each family sees it opening with their own
name and signed off with the school's sender name, so start with what you
want to say.

The message has a small formatting toolbar: bold, italic and underline, two
heading sizes, numbered and bulleted lists, links, and pictures. Keep it to
that — it is exactly what email programs show reliably, and anything pasted
in from elsewhere is reduced to the same set before it is sent.

To add a picture, press the picture button and choose a file, or paste or
drag one into the message. It is uploaded straight away (up to 5 MB, shrunk
to a sensible size for an email) and appears where your cursor was. The
email links to the hosted copy rather than attaching it.

> The email keeps your formatting and pictures. The WhatsApp message carries
> the words only — a picture becomes its description, a link its address — so
> never let a picture carry the whole point.

## Writing to one class {#one-class}

![The composer for one class, each audience with the number of families it reaches](class-announcement.png)

The **Announce** link on a class opens the same editor, already addressed to
that class. Nothing else changes: the same audiences, the same test email, the
same delivery log, and the message is recorded in the announcement history
like any other.

Two things it does that the main composer cannot:

- **A cancelled class.** Cancelling a class cancels every place in it, so
  *Everyone with a live place* matches nobody there. Pick **Everyone who ever
  had a place** and the families who were in it hear from you — to apologise,
  to offer an alternative, or to explain a refund.
- **A class whose term is over.** The main composer lists the active term
  only, on purpose: a picker holding every class ever run is a picker nobody
  can read. Last term's class is still on the class list, and its **Announce**
  link still works.

Each audience on that page carries the number of families it would reach,
counted for the class as it stands, so you can see before you write that
*Everyone with a live place — 0 families* and pick the one that isn't empty.

> Messages to several classes at once still belong in the main composer. This
> one sends to the class you opened it from and nothing else.

## Who actually gets it {#recipients}

> This surprises people, so it is worth reading once.
>
> With **Everyone with a live place**, an announcement goes to **every guardian
> of every child who has a live place in the classes you picked** — and "live"
> includes children whose request you have not reviewed yet, children on the
> waiting list, and children holding an offer. Not only the enrolled ones.

That is usually what you want: a cancelled first session concerns everyone who
thinks they might be coming. But if a message only makes sense for confirmed
children, say so in the message itself.

**Waiting list only** is the narrow one: the guardians of the children sitting
at status *On waiting list* in those classes, and nobody else. A child you have
already offered a seat to has left the list — they are being asked to confirm,
and the offer email is doing that job — so an offer holder is not written to
here. Neither is a request you have not approved yet: until you approve it, the
child is not on the list.

**Everyone who ever had a place** is the widest: it adds the cancelled places
to the live ones, so a family who withdrew in week two hears about it too.
Worth a thought before you use it on a class that is still running — those
families chose to leave.

If nothing matches — an empty waiting list, say — nothing is sent, and the page
tells you so instead of reporting a send.

Nobody is emailed twice. A parent with two children in two of the classes you
picked gets one email.

## Sending {#send}

First, press **Send me a test email** under the message. The email arrives in
your own inbox exactly as a family will see it — opening with your name where
theirs will be — so you can check the wording, the pictures and how it looks
on your phone. Send yourself as many tests as you like; nothing is recorded.

Then press save and it goes. You are told how many families it reached:
*Announcement queued for 24 families.*

Sent announcements record who they were addressed to — the classes and the
audience — so the history says whether a message went to a whole class or to
its waiting list.

> There is no draft and no unsend. The test email is your preview: read it
> once more before you press the button.

Sent announcements are kept as a record. You can open one to see who it went
to and when, but you cannot edit it.

## Checking it arrived {#delivery}

**Notifications → Notifications** is the delivery log: one row per person per
channel, with a status.

![The notifications delivery log](notifications-log.png)

- **Sent** — gone.
- **Failed** — something went wrong. Tick the rows and use the action *Retry
  failed notifications*.
- **Skipped** — deliberately not sent, and the reason is written out, for
  example *"No phone number on profile"* for a WhatsApp row. The email will
  usually have gone anyway.

## Changing the standard emails {#templates}

Announcements are the message *you* write. Everything else the app sends —
"we've got your registration", "confirmed", "a place has opened up" — is a
template you can edit at **Notifications → Notification templates**, one per
event. Change the wording there and it applies to everything sent from then
on.

## Providers can write too {#providers}

Coaches and tutors have their own **Message families** button on their
dashboard, limited to their own classes. They choose from the same three
audiences, and their messages appear in the same delivery log.
