"""Sets up Jinja2, the library that fills HTML templates in the templates/ folder."""

from fastapi.templating import Jinja2Templates  # FastAPI's helper for rendering Jinja2 templates.

from core.config import PROJECT_ROOT, get_settings  # Project folder and settings.

# Tells Jinja2 where the HTML files live. Templates automatically escape HTML, which blocks injected scripts.
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))


def render(request, template_name, context=None, status_code=200):
    """Renders an HTML template and returns it as a web response.

    request: the current FastAPI request (templates use it to read the session and build URLs).
    template_name: a file name inside templates/, for example "dashboard.html".
    context: a dictionary of values the template can display.
    status_code: the HTTP status, for example 200 for OK or 404 for Not Found.
    """
    if context is None:
        context = {}
    settings = get_settings()
    # Values every page needs, such as the header title and the logged-in user.
    context["app_title"] = settings["app_title"]
    context["default_region"] = settings["aws_region"]
    context["writes_allowed"] = settings["allow_write_operations"]
    context["username"] = request.session.get("username")
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def render_not_found(request, message):
    # Shows the friendly error page with a "404 Not Found" status.
    return render(request, "error.html", {"message": message}, status_code=404)
