import datetime

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import SignupForm
from .models import Guardian, GuardianInvite, SiteConfig, User

INVITE_TTL_DAYS = 14


def _safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return None


def signup(request):
    if not SiteConfig.get().signup_open:
        messages.info(request, "Account creation is currently handled by the school office.")
        return redirect("login")
    if request.user.is_authenticated:
        return redirect("post_login")

    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Welcome! Start by adding your children to your family.")
        # Honor ?next= so flows like guardian invites continue after signup.
        return redirect(_safe_next(request) or "parent_home")
    return render(
        request,
        "registration/signup.html",
        {"form": form, "next": request.GET.get("next", "")},
    )


@login_required
def post_login(request):
    """Send each role to its home page after login."""
    role = request.user.role
    if role == User.Role.PROVIDER:
        return redirect("provider_home")
    if role == User.Role.ADMIN:
        return redirect("admintools_requests")
    return redirect("parent_home")


def _valid_invite_or_404(token):
    cutoff = timezone.now() - datetime.timedelta(days=INVITE_TTL_DAYS)
    return get_object_or_404(
        GuardianInvite,
        token=token,
        accepted_at__isnull=True,
        created_at__gte=cutoff,
    )


@login_required
def accept_guardian_invite(request, token):
    invite = _valid_invite_or_404(token)
    if request.user.role != User.Role.PARENT:
        messages.error(request, "Only parent accounts can accept a co-parent invitation.")
        return redirect("post_login")
    # The invite grants guardianship over a child — only the account with the
    # invited email address may accept it, so a forwarded/leaked link is inert.
    if request.user.email.lower() != invite.email.lower():
        messages.error(
            request,
            f"This invitation was sent to {invite.email}. Log in with that "
            "account to accept it, or ask for a new invitation to your address.",
        )
        return redirect("post_login")

    if request.method == "POST":
        Guardian.objects.get_or_create(child=invite.child, user=request.user)
        invite.accepted_at = timezone.now()
        invite.accepted_by = request.user
        invite.save(update_fields=["accepted_at", "accepted_by"])
        messages.success(
            request, f"You can now manage {invite.child.full_name}'s activities."
        )
        return redirect("parent_home")

    return render(request, "registration/invite_accept.html", {"invite": invite})


def invite_landing(request, token):
    """Public landing for invite links: route to login/signup preserving the token."""
    invite = _valid_invite_or_404(token)
    if request.user.is_authenticated:
        return redirect("accept_guardian_invite", token=token)
    next_url = reverse("accept_guardian_invite", kwargs={"token": token})
    return render(
        request,
        "registration/invite_landing.html",
        {"invite": invite, "next_url": next_url},
    )
