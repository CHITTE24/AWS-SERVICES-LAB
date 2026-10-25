"""Experiment: S3 Storage Report. Finds each bucket's region, then counts a sample of its objects."""

from experiments.steps import run_step  # Shared experiment helper.
from integrations import s3  # The only service this experiment uses.


def run(max_buckets=10):
    """Inspects up to max_buckets buckets.

    max_buckets: limits how many buckets are inspected, which keeps the experiment fast and cheap.
    Returns: an experiment result dictionary (see experiments/steps.py).
    """
    errors = []
    buckets = run_step(errors, "List S3 buckets", s3.list_buckets)
    if buckets is None:
        # Without the bucket list there is nothing more to do.
        return {"summary": {}, "tables": [], "notes": [], "errors": errors}

    rows = []
    total_bytes = 0
    # buckets[:max_buckets] means "the first max_buckets items of the list".
    for bucket in buckets[:max_buckets]:
        name = bucket["name"]
        region = run_step(errors, f"Region of {name}", s3.get_bucket_region, name)
        counts = run_step(errors, f"Objects in {name}", s3.count_objects, name, region)

        row = {"bucket": name, "region": region or "unknown", "objects_counted": "unknown",
               "size_mb": "unknown", "more_objects_exist": ""}
        if counts is not None:
            row["objects_counted"] = counts["objects_counted"]
            # 1 MB = 1024 * 1024 bytes. round(..., 2) keeps two decimal places.
            row["size_mb"] = round(counts["total_bytes"] / (1024 * 1024), 2)
            row["more_objects_exist"] = "yes" if counts["more_objects_exist"] else "no"
            total_bytes = total_bytes + counts["total_bytes"]
        rows.append(row)

    summary = {
        "Buckets in account": len(buckets),
        "Buckets inspected": len(rows),
        "Total size counted (MB)": round(total_bytes / (1024 * 1024), 2),
    }
    notes = ["Only the first 1000 objects in each bucket are counted, which keeps this experiment cheap."]
    return {"summary": summary, "tables": [{"title": "Buckets", "rows": rows}], "notes": notes, "errors": errors}
