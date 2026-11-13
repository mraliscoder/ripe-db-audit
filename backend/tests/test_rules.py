import pytest
from app.models import bounds, parse_object
from app.rules import analyze

REFERENCE = "2026-11-13T00:00:00Z"


def obj(resource, status, assignment_size="", created="2020-01-01T00:00:00Z"):
    typ = "aut-num" if resource.startswith("AS") else "inet6num" if ":" in resource else "inetnum"
    attrs = [(typ, resource), ("status", status), ("org", "ORG-TEST-RIPE"), ("created", created)]
    if assignment_size:
        attrs.append(("assignment-size", assignment_size))
    return parse_object(
        {"type": typ, "attributes": {"attribute": [{"name": k, "value": v} for k, v in attrs]}},
        "test",
    )


def run(rows, prefixes=(), activity=None):
    findings, summary = analyze(
        rows, [{"prefix": p, "origins": ["AS65000"]} for p in prefixes], activity or {}, REFERENCE
    )
    return [f["code"] for f in findings], summary


def test_ipv4_non_cidr_ranges_are_not_fabricated_objects():
    start, end, family, display = bounds("192.0.2.3 - 192.0.2.19")
    assert end - start + 1 == 17 and family == 4
    assert display == "192.0.2.3 - 192.0.2.19"


def test_allocated_assigned_pa_is_already_a_valid_assignment():
    codes, _ = run([obj("192.0.2.0/24", "ALLOCATED-ASSIGNED PA")], ["192.0.2.0/24"])
    assert not codes


def test_ipv4_aggregated_assignment_size_is_optional():
    codes, _ = run(
        [obj("192.0.2.0/24", "ALLOCATED PA"), obj("192.0.2.0/25", "AGGREGATED-BY-LIR")],
        ["192.0.2.0/24"],
    )
    assert not codes


def test_existing_ipv4_allocations_are_not_subject_to_new_24_cap():
    codes, _ = run([obj("192.0.0.0/16", "ALLOCATED PA"), obj("192.0.0.0/16", "AGGREGATED-BY-LIR")])
    assert not codes


def test_suballocation_requires_pa_ancestor():
    codes, _ = run(
        [obj("192.0.2.0/24", "ALLOCATED UNSPECIFIED"), obj("192.0.2.0/25", "SUB-ALLOCATED PA")]
    )
    assert "ipv4_invalid_suballocation_parent" in codes


def test_pi_inside_pa_suballocation_is_flagged():
    codes, _ = run(
        [
            obj("192.0.2.0/24", "ALLOCATED PA"),
            obj("192.0.2.0/25", "SUB-ALLOCATED PA"),
            obj("192.0.2.0/26", "ASSIGNED PI"),
        ]
    )
    assert "ipv4_pi_in_pa_suballocation" in codes


def test_ipv4_terminal_assigned_objects_cannot_nest_assigned():
    codes, _ = run([obj("192.0.2.0/24", "ASSIGNED PA"), obj("192.0.2.0/25", "ASSIGNED PA")])
    assert "ipv4_nested_assignment" in codes


def test_more_specific_announcements_are_checked_individually():
    codes, _ = run(
        [
            obj("2001:db8::/32", "ALLOCATED-BY-RIR"),
            obj("2001:db8::/44", "ALLOCATED-BY-LIR"),
            obj("2001:db8::/48", "ASSIGNED"),
        ],
        ["2001:db8::/44", "2001:db8:1::/48"],
    )
    assert codes.count("announced_without_assignment") == 1


def test_large_ipv6_aggregate_with_48_assignments_is_valid():
    codes, _ = run(
        [
            obj("2001:db8::/32", "ALLOCATED-BY-RIR"),
            obj("2001:db8::/40", "ALLOCATED-BY-LIR"),
            obj("2001:db8::/44", "AGGREGATED-BY-LIR", "48"),
        ],
        ["2001:db8:1::/48"],
    )
    assert not codes


def test_invalid_aggregate_cannot_satisfy_announced_registration():
    codes, _ = run(
        [obj("2001:db8::/40", "ALLOCATED-BY-LIR"), obj("2001:db8::/44", "AGGREGATED-BY-LIR", "45")],
        ["2001:db8::/48"],
    )
    assert "invalid_aggregated_assignment_size" in codes
    assert "announced_without_assignment" in codes


def test_sixth_allocated_by_lir_is_flagged():
    rows = [obj(f"2001:db8::/{length}", "ALLOCATED-BY-LIR") for length in range(33, 39)]
    codes, _ = run(rows)
    assert codes.count("allocated_lir_depth_exceeded") == 1


def test_recent_asn_creation_prevents_historical_false_positive():
    codes, _ = run(
        [obj("AS65000", "ASSIGNED", created="2026-10-01T00:00:00Z")],
        activity={
            "AS65000": {"recent": False, "last_seen": "2020-01-01T00:00:00Z", "current_origins": 0}
        },
    )
    assert not codes


def test_old_inactive_asn_is_flagged():
    codes, _ = run(
        [obj("AS65000", "ASSIGNED")],
        activity={"AS65000": {"recent": False, "last_seen": None, "current_origins": 0}},
    )
    assert codes == ["asn_inactive_over_3_months"]


@pytest.mark.parametrize(
    "recent,last_seen,current,state,violation",
    [
        (True, "2026-11-01T00:00:00Z", 0, "inactive_under_three_months", False),
        (True, "2026-11-13T00:00:00Z", 1, "recently_announced", False),
        (False, "2026-07-01T00:00:00Z", 0, "inactive_over_three_months", True),
        (False, "2026-08-13T00:00:00Z", 0, "inactive_under_three_months", False),
    ],
)
def test_asn_activity_distinguishes_current_from_recent(
    recent, last_seen, current, state, violation
):
    row = obj("AS65000", "ASSIGNED")
    codes, _ = run(
        [row],
        activity={
            "AS65000": {
                "recent": recent,
                "last_seen": last_seen,
                "current_origins": current,
            }
        },
    )
    assert row["activity"] == state
    assert row["bgp_state"] == ("exact" if current else "none")
    assert bool(codes) == violation
