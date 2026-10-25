"""Configures where log messages go: the terminal and the file logs/app.log."""

import logging  # Python's built-in logging system.


def setup_logging(level_name, log_file):
    """Sends log messages to the terminal and to a log file.

    level_name: "DEBUG", "INFO", "WARNING", or "ERROR". Lower levels show more detail.
    log_file: a pathlib.Path for the log file. Its folder is created if missing.
    """
    # Makes sure the logs/ folder exists before writing to it.
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # Converts a text level such as "INFO" into logging's number (INFO = 20). Unknown text means INFO.
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        # Each line looks like: 2026-01-01 12:00:00 INFO routes.dashboard_routes: message
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),  # Prints to the terminal.
            logging.FileHandler(log_file, encoding="utf-8"),  # Appends to logs/app.log.
        ],
        force=True,  # Replaces any logging setup done earlier, for example by uvicorn.
    )
