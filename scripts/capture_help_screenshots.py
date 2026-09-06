#!/usr/bin/env python3
"""Re-capture the screenshots used by the in-app help pages.

Screenshots go stale the moment a template moves, so they are generated rather
than cropped by hand: this builds a throwaway database, fills it with a
plausible office day, drives the real admin in headless Chromium, and writes
the PNGs the guides reference by name.

    pip install -e ".[screenshots]"
    playwright install chromium
    python scripts/capture_help_screenshots.py

Nothing in the app depends on this script at runtime; the PNGs it writes are
committed. Run it whenever an admin screen changes, then commit the diff.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = BASE_DIR / "static" / "img" / "help" / "admin"
PASSWORD = "demo1234"
ADMIN_EMAIL = "admin@school.test"
VIEWPORT = {"width": 1500, "height": 950}
# Matches config.settings.base.TIME_ZONE, so the admin does not warn about clocks.
SERVER_TIMEZONE = os.environ.get("TIME_ZONE", "Europe/Malta")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_database(env: dict) -> str:
    """A demo term, then the states an office actually looks at.

    Returns the id of the class whose roster is worth photographing.
    """
    for args in (["migrate", "--noinput"], ["seed_demo"]):
        subprocess.run(
            [sys.executable, "manage.py", *args],
            cwd=BASE_DIR, env=env, check=True, stdout=subprocess.DEVNULL,
        )
    office = subprocess.run(
        [sys.executable, "scripts/demo_office.py"],
        cwd=BASE_DIR, env=env, check=True, capture_output=True, text=True,
    )
    return office.stdout.strip().splitlines()[-1]


def serve(env: dict, port: int):
    server = subprocess.Popen(
        [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
        cwd=BASE_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return server
        except OSError:
            if server.poll() is not None:
                raise SystemExit("the development server exited before it was ready")
            time.sleep(0.2)
    server.terminate()
    raise SystemExit("the development server never came up")


def shrink(path: Path) -> None:
    """Re-save as a palette PNG when that is smaller.

    A screenshot of flat admin UI is a few dozen colours, so 192 of them are
    indistinguishable from the truecolour capture at about a third of the
    bytes — worth having, for pictures that live in the repository. Pillow is
    already a dependency.
    """
    from PIL import Image

    candidate = path.with_suffix(".palette.png")
    Image.open(path).convert("RGB").quantize(colors=192).save(candidate, optimize=True)
    if candidate.stat().st_size < path.stat().st_size:
        candidate.replace(path)
    else:
        candidate.unlink()


def shoot(page, name: str, selector: str = "#content", max_height: int | None = None) -> None:
    """One element, optionally cut off at `max_height` CSS pixels.

    A changelist can run to hundreds of rows; a help page only ever needs the
    top of it, and a 2 MB PNG helps nobody.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    element = page.locator(selector).first
    element.scroll_into_view_if_needed()
    # bounding_box() is viewport-relative; back at the top of the page it is
    # also page-relative, which is what a full-page clip needs.
    page.evaluate("window.scrollTo(0, 0)")
    box = element.bounding_box()
    if max_height and box and box["height"] > max_height:
        # full_page: the clip is in page coordinates, which for a tall element
        # reach past the bottom of the viewport.
        page.screenshot(
            path=str(OUT_DIR / name), clip={**box, "height": max_height}, full_page=True
        )
    else:
        element.screenshot(path=str(OUT_DIR / name))
    shrink(OUT_DIR / name)
    print(f"  {name}")


def capture(base_url: str, roster_class_id: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        # PLAYWRIGHT_CHROMIUM_EXECUTABLE is the escape hatch for a machine that
        # already has a Chromium Playwright did not download itself.
        browser = playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE") or None
        )
        # Same timezone as the server, or the admin stamps every datetime
        # widget with "you are N hours behind server time".
        page = browser.new_context(
            viewport=VIEWPORT, device_scale_factor=2, timezone_id=SERVER_TIMEZONE
        ).new_page()

        page.goto(f"{base_url}/admin/login/")
        page.fill("#id_username", ADMIN_EMAIL)
        page.fill("#id_password", PASSWORD)
        page.click("input[type=submit]")
        page.wait_for_url(f"{base_url}/admin/**")

        print("capturing:")

        page.goto(f"{base_url}/admin/enrollments/enrollment/requests/")
        shoot(page, "requests.png", max_height=760)
        shoot(page, "request-row.png", "table.desk-table >> nth=0", max_height=250)
        shoot(page, "cancellation-requests.png", "table.desk-table >> nth=1")

        page.goto(f"{base_url}/admin/catalog/activityclass/?term__is_active__exact=1")
        shoot(page, "class-list.png", "#result_list")
        page.select_option("select[name=action]", "regenerate_sessions")
        shoot(page, "class-actions.png", "#changelist-form .actions")

        page.goto(f"{base_url}/admin/catalog/activityclass/{roster_class_id}/change/")
        shoot(page, "class-form.png", "#activityclass_form fieldset >> nth=0", max_height=700)
        shoot(page, "class-sessions.png", "#sessions-group", max_height=520)

        page.goto(f"{base_url}/admin/catalog/activityclass/{roster_class_id}/roster/")
        shoot(page, "roster.png", max_height=900)
        shoot(page, "roster-waiting-list.png", "#waiting-list")

        page.goto(f"{base_url}/admin/notifications/broadcast/add/")
        shoot(page, "announcement-form.png", max_height=980)

        page.goto(f"{base_url}/admin/notifications/notification/")
        shoot(page, "notifications-log.png", "#result_list", max_height=520)

        browser.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as workspace:
        env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings.dev",
            "DATABASE_URL": f"sqlite:///{Path(workspace) / 'help.sqlite3'}",
            "MEDIA_ROOT": str(Path(workspace) / "media"),
            "NOTIFIER_INLINE_DELIVERY": "false",
            "PYTHONPATH": str(BASE_DIR),
        }
        roster_class_id = build_database(env)
        port = free_port()
        server = serve(env, port)
        try:
            capture(f"http://127.0.0.1:{port}", roster_class_id)
        finally:
            server.terminate()
            server.wait(timeout=10)
    print(f"\nwritten to {OUT_DIR.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
