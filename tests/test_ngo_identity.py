import re

from ngo_identity import ensure_ngo_id, generate_ngo_id, get_ngo_id


def test_id_format_and_stability_from_source_record():
    row = {
        "source_record_id": "KA-DARPAN-0042",
        "name": "Example Children Trust",
        "district": "Mysuru",
        "state": "Karnataka",
    }
    first = get_ngo_id(row)
    second = get_ngo_id({**row, "website": "https://new-site.example.org"})
    assert first == second
    assert re.fullmatch(r"DFP-NGO-[A-F0-9]{16}", first)


def test_distinct_source_records_receive_distinct_ids_even_with_same_name_district_and_registration():
    common = {
        "name": "Same Name Trust",
        "district": "Bengaluru Urban",
        "state": "Karnataka",
        "registration_reference": "22",
    }
    assert get_ngo_id({**common, "source_record_id": "SRC-1"}) != get_ngo_id({**common, "source_record_id": "SRC-2"})


def test_source_record_outweighs_reused_registration_reference():
    row_one = {
        "registration_reference": "22",
        "state": "Karnataka",
        "source_record_id": "source-row-1",
        "name": "First Trust",
    }
    row_two = {
        "registration_reference": "22",
        "state": "Karnataka",
        "source_record_id": "source-row-2",
        "name": "Second Society",
    }
    assert get_ngo_id(row_one) != get_ngo_id(row_two)


def test_registration_fallback_is_stable_when_source_and_lead_ids_are_absent():
    row_one = {
        "registration_reference": "REG/2020/123",
        "name": "Example Children Trust",
        "district": "Mysuru",
        "state": "Karnataka",
    }
    row_two = {**row_one, "website": "https://example.org/new-page"}
    assert get_ngo_id(row_one) == get_ngo_id(row_two)


def test_placeholder_registration_does_not_merge_unrelated_rows():
    first = {"registration_reference": "NA", "name": "First Trust", "district": "Mysuru", "state": "Karnataka"}
    second = {"registration_reference": "NA", "name": "Second Trust", "district": "Mysuru", "state": "Karnataka"}
    assert get_ngo_id(first) != get_ngo_id(second)


def test_existing_valid_id_is_never_replaced():
    existing = "DFP-NGO-0123456789ABCDEF"
    row = {"ngo_id": existing, "source_record_id": "different-source"}
    assert generate_ngo_id(row) == existing
    assert ensure_ngo_id(row) == existing
    assert row["ngo_id"] == existing
