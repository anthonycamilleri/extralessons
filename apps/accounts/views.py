from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import SignupForm
from .models import Guardian, GuardianInvite, SiteConfig, User


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
        return redirect("parent_home")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def post_login(request):
    """Send each role to its home page after login."""
    role = request.user.role
    if role == User.Role.PROVIDER:
        return redirect("provider_home")
    if role == User.Role.ADMIN:
        return redirect("admintools_requests")
    return redirect("parent_home")


@login_required
def accept_guardian_invite(request, token):
    invite = get_object_or_404(GuardianInvite, token=token, accepted_at__isnull=True)
    if request.user.role != User.Role.PARENT:
        messages.error(request, "Only parent accounts can accept a co-parent invitation.")
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
    invite = get_object_or_404(GuardianInvite, token=token, accepted_at__isnull=True)
    if request.user.is_authenticated:
        return redirect("accept_guardian_invite", token=token)
    next_url = reverse("accept_guardian_invite", kwargs={"token": token})
    return render(
        request,
        "registration/invite_landing.html",
        {"invite": invite, "next_url": next_url},
    )
