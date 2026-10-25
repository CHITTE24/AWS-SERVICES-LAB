"""Checks that the service catalog and experiment registry are consistent, and that form validation works."""

import pytest

from catalog.categories import get_category_ids
from catalog.services import SERVICES, get_operation, get_service
from core.inputs import clean_inputs
from experiments.registry import EXPERIMENTS, get_experiment


def test_every_service_is_well_formed():
    category_ids = get_category_ids()
    seen_ids = []
    for service in SERVICES:
        # Each service needs these keys, a known category, and a unique id.
        for key in ("id", "name", "category", "description", "operations"):
            assert key in service, f"{service.get('id')} is missing {key}"
        assert service["category"] in category_ids
        assert service["id"] not in seen_ids
        seen_ids.append(service["id"])

        for operation in service["operations"]:
            assert operation["kind"] in ("read", "write")
            assert callable(operation["function"])


def test_lookup_helpers():
    assert get_service("s3")["name"] == "Amazon S3"
    assert get_service("does-not-exist") is None
    assert get_operation(get_service("s3"), "list_buckets") is not None
    assert get_experiment("account_overview") is not None
    assert get_experiment("nope") is None


def test_every_experiment_is_well_formed():
    for experiment in EXPERIMENTS:
        assert callable(experiment["run"])
        assert experiment["services"]


NUMBER_FIELD = {"name": "hours", "label": "Hours", "type": "number", "default": 24, "min": 1, "max": 168}
REGION_FIELD = {"name": "region", "label": "Region", "type": "region"}
REQUIRED_FIELD = {"name": "bucket_name", "label": "Bucket name", "type": "text", "required": True}


def test_clean_inputs_uses_defaults_and_converts_numbers():
    assert clean_inputs([NUMBER_FIELD, REGION_FIELD], {}) == {"hours": 24, "region": None}
    assert clean_inputs([NUMBER_FIELD], {"hours": " 48 "}) == {"hours": 48}


def test_clean_inputs_rejects_bad_values():
    # pytest.raises checks that the code inside raises the expected error.
    with pytest.raises(ValueError):
        clean_inputs([NUMBER_FIELD], {"hours": "999"})
    with pytest.raises(ValueError):
        clean_inputs([NUMBER_FIELD], {"hours": "abc"})
    with pytest.raises(ValueError):
        clean_inputs([REGION_FIELD], {"region": "not a region"})
    with pytest.raises(ValueError):
        clean_inputs([REQUIRED_FIELD], {})


def test_clean_inputs_accepts_valid_region():
    assert clean_inputs([REGION_FIELD], {"region": "eu-west-2"}) == {"region": "eu-west-2"}
