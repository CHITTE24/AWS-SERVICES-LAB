"""The groups used to organise services on the dashboard. Add a dictionary here to add a category."""

CATEGORIES = [
    {"id": "compute", "name": "Compute", "description": "Run servers and code."},
    {"id": "storage", "name": "Storage", "description": "Store files and objects."},
    {"id": "database", "name": "Database", "description": "Managed databases."},
    {"id": "security", "name": "Security & Identity", "description": "Who can do what in your account."},
    {"id": "monitoring", "name": "Monitoring", "description": "Metrics, alarms, and logs."},
    {"id": "integration", "name": "Application Integration", "description": "Queues and notifications."},
]


def get_category_ids():
    # Returns just the ids, for example ["compute", "storage", ...]. Tests use it to check the catalog.
    ids = []
    for category in CATEGORIES:
        ids.append(category["id"])
    return ids
