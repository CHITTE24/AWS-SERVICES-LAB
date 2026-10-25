"""Login and logout pages."""

import logging  # Records login attempts in logs/app.log.
import time  # Used to slow down repeated wrong guesses.

from fastapi import APIRouter, Form, Request  # Router groups routes; Form reads HTML form fields.
from fastapi.responses import RedirectResponse  # Sends the browser to a different URL.

from auth.access import check_credentials  # Checks the username and password.
from core.templates import render  # Renders HTML templates.

router = APIRouter()  # main.py adds this router to the app.
logger = logging.getLogger(__name__)


@router.get("/login")
def login_page(request: Request):
    # Shows the empty login form.
    return render(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    # Form(...) means the value comes from the submitted HTML form and is required.
    if check_credentials(username, password):
        # Clears any old session data, then remembers who logged in. The cookie is signed, so it cannot be faked.
        request.session.clear()
        request.session["username"] = username
        logger.info("Login succeeded for %s", username)
        # 303 tells the browser to load the dashboard with a normal GET request.
        return RedirectResponse("/", status_code=303)

    # Never log the typed password. Only the username is recorded.
    logger.warning("Login failed for %s", username)
    # A short pause makes guessing many passwords slow.
    time.sleep(1)
    return render(request, "login.html", {"error": "Wrong username or password."}, status_code=401)


@router.post("/logout")
def logout(request: Request):
    # Forgets the logged-in user. Logout uses POST so another website cannot log you out with a link.
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
