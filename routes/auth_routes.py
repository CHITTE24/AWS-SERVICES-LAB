"""Login and logout pages (demonstration login: see auth/access.py)."""

import logging  # Records logins in logs/app.log.
from datetime import datetime, timezone  # The login time saved in the session.

from fastapi import APIRouter, Form, Request  # Router groups routes; Form reads HTML form fields.
from fastapi.responses import RedirectResponse  # Sends the browser to a different URL.

from auth.access import check_login_form  # Checks that both fields are filled in.
from core.templates import render  # Renders HTML templates.

router = APIRouter()  # main.py adds this router to the app.
logger = logging.getLogger(__name__)


@router.get("/login")
def login_page(request: Request):
    # Shows the empty login form.
    return render(request, "login.html", {"error": None, "typed_username": ""})


@router.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form("")):
    # Form("") reads the field from the submitted HTML form. A missing field becomes "" so we can explain it.
    error = check_login_form(username, password)
    if error:
        # Shows the form again. The username is kept, but the password is never sent back to the browser.
        context = {"error": error, "typed_username": username}
        return render(request, "login.html", context, status_code=400)

    # Clears any old session data, then remembers who logged in and when. The cookie is signed, so it cannot be faked.
    # The PASSWORD is not stored anywhere: not in the cookie, the database, the logs, or AWS.
    request.session.clear()
    request.session["username"] = username.strip()
    request.session["login_time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info("Demo login for %s", username.strip())
    # 303 tells the browser to load the main page with a normal GET request.
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    # Forgets the logged-in user. Logout uses POST so another website cannot log you out with a link.
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
