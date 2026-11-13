import collections
import hashlib
import ipaddress

from .models import bounds, contained, cutoff, hierarchy, moment, overlaps, ripe_url

POLICY = "https://www.ripe.net/publications/docs/ripe-826/"


def analyze(resources, routes, asn_activity, reference):
    ips = hierarchy(resources)
    findings = []

    def add(row, code, ru, en, *, severity="error", basis="audit_rule", source=None, route=None):
        fingerprint = "|".join((row["id"], code, route["prefix"] if route else ""))
        finding = {
            "id": hashlib.sha256(fingerprint.encode()).hexdigest()[:20],
            "resource_id": row["id"],
            "resource": row["resource"],
            "family": row["family"],
            "org_handles": row["org_handles"],
            "level1_id": row.get("level1_id"),
            "code": code,
            "message": en,
            "message_en": en,
            "severity": severity,
            "basis": basis,
            "source_url": source,
            "announced_prefix": route["prefix"] if route else None,
            "origins": route["origins"] if route else [],
        }
        findings.append(finding)
        row["findings"].append(finding["id"])

    def aggregated_valid(row):
        value = row["assignment_size"]
        if row["family"] == "ipv4":
            return not value or (
                value.isascii()
                and value.isdecimal()
                and 0 <= int(value) <= 32
                and 2 ** (32 - int(value)) <= row["end"] - row["start"] + 1
            )
        length = ipaddress.ip_network(row["resource"]).prefixlen
        return value.isascii() and value.isdecimal() and max(48, length) <= int(value) <= 64

    for row in ips:
        status = row["status"]
        if row["family"] == "ipv6":
            length = ipaddress.ip_network(row["resource"]).prefixlen
            if status == "ASSIGNED" and not 48 <= length <= 64:
                add(
                    row,
                    "invalid_assigned_state",
                    f"ASSIGNED /{length}: по правилам аудита допустимы /48–/64.",
                    f"Invalid assigned state - /{length}, allowed /48–/64.",
                )
            if status == "ALLOCATED-BY-LIR" and row["allocation_depth"] > 5:
                add(
                    row,
                    "allocated_lir_depth_exceeded",
                    "В цепочке более пяти ALLOCATED-BY-LIR.",
                    "More than five ALLOCATED-BY-LIR objects in ancestry.",
                )
        if status == "AGGREGATED-BY-LIR" and not aggregated_valid(row):
            add(
                row,
                "invalid_aggregated_assignment_size",
                "Некорректный assignment-size: /48–/64 для IPv6; для IPv4 поле необязательно, указанный блок должен помещаться в агрегат.",
                "Invalid aggregated assignment-size.",
                basis="audit_rule" if row["family"] == "ipv6" else "database_schema",
                source=None
                if row["family"] == "ipv6"
                else "https://docs.db.ripe.net/RPSL-Object-Types/Descriptions-of-Primary-Objects/",
            )
        if row["family"] != "ipv4":
            continue
        ancestors = [p for p in ips if p["id"] != row["id"] and contained(row, p)]
        descendants = [p for p in ips if p["id"] != row["id"] and contained(p, row)]
        if status == "SUB-ALLOCATED PA" and not any(
            p["status"] == "ALLOCATED PA" for p in ancestors
        ):
            add(
                row,
                "ipv4_invalid_suballocation_parent",
                "SUB-ALLOCATED PA не находится внутри ALLOCATED PA в собранной иерархии.",
                "SUB-ALLOCATED PA requires an ALLOCATED PA ancestor in the collected scope.",
                basis="ripe_policy",
                source=POLICY + "#53-sub-allocations",
            )
        if status == "ASSIGNED PI" and any(p["status"] == "SUB-ALLOCATED PA" for p in ancestors):
            add(
                row,
                "ipv4_pi_in_pa_suballocation",
                "В SUB-ALLOCATED PA допустимы назначения PA, а не PI.",
                "A PA suballocation contains an ASSIGNED PI registration.",
                basis="ripe_policy",
                source=POLICY + "#53-sub-allocations",
            )
        if status == "ASSIGNED PA" and any(
            p["status"] in ("ALLOCATED PI", "ALLOCATED UNSPECIFIED") for p in ancestors
        ):
            add(
                row,
                "ipv4_pa_in_portable_allocation",
                "Назначение PA находится в allocation с portable-статусом.",
                "PA assignment inside a portable allocation.",
                basis="ripe_policy",
                source=POLICY + "#70-types-of-address-space",
            )
        if status in ("ASSIGNED PA", "ASSIGNED PI", "ALLOCATED-ASSIGNED PA") and any(
            p["status"] in ("ASSIGNED PA", "ASSIGNED PI", "ALLOCATED-ASSIGNED PA")
            for p in descendants
        ):
            add(
                row,
                "ipv4_nested_assignment",
                "Объект с конечным назначением содержит другое ASSIGNED-назначение.",
                "An assigned IPv4 object contains another assigned object.",
                basis="ripe_policy",
                source=POLICY + "#70-types-of-address-space",
            )

    registrations = [
        r
        for r in ips
        if r["status"]
        in ("ASSIGNED", "ASSIGNED PI", "ASSIGNED ANYCAST", "ASSIGNED PA", "ALLOCATED-ASSIGNED PA")
        or (r["status"] == "AGGREGATED-BY-LIR" and aggregated_valid(r))
    ]
    allocations = [
        r
        for r in ips
        if r["status"]
        in ("ALLOCATED-BY-LIR", "ALLOCATED PA", "SUB-ALLOCATED PA", "LIR-PARTITIONED PA")
    ]
    routing = []
    for route in routes:
        start, end, version, _ = bounds(route["prefix"])
        routing.append({**route, "start": start, "end": end, "family": f"ipv{version}"})
    checks = 0
    for allocation in allocations:
        for route in routing:
            if not overlaps(allocation, route):
                continue
            checks += 1
            if not any(
                contained(reg, allocation) and overlaps(reg, route) for reg in registrations
            ):
                v4 = allocation["family"] == "ipv4"
                add(
                    allocation,
                    "announced_without_assignment",
                    f"Для анонса {route['prefix']} внутри {allocation['resource']} не найдено ASSIGNED или корректного AGGREGATED-BY-LIR.",
                    f"Announcement {route['prefix']} has no assignment registration within {allocation['resource']}.",
                    route=route,
                    severity="warning" if v4 else "error",
                    basis="audit_rule",
                    source=POLICY + "#62-network-infrastructure-and-end-user-networks"
                    if v4
                    else None,
                )

    ref = moment(reference)
    for row in (r for r in resources if r["family"] == "asn"):
        activity = asn_activity.get(row["resource"])
        if not activity:
            row["activity"] = "unknown"
            continue
        row.update(activity)
        last = moment(activity["last_seen"]) if activity.get("last_seen") else None
        created = moment(row["created"]) if row.get("created") else None
        recent = activity["recent"] or (last and last >= cutoff(ref))
        if activity.get("current_origins"):
            row["activity"] = "recently_announced"
        elif recent:
            row["activity"] = "inactive_under_three_months"
        elif created is None:
            row["activity"] = "unknown_creation_date"
        elif created >= cutoff(ref):
            row["activity"] = "younger_than_three_months"
        else:
            row["activity"] = "inactive_over_three_months"
            add(
                row,
                "asn_inactive_over_3_months",
                "Нет наблюдаемых origin-анонсов IPv4/IPv6 более трёх календарных месяцев.",
                "No observed originated IPv4/IPv6 prefixes for more than three calendar months.",
            )
        row["bgp_state"] = "exact" if activity.get("current_origins") else "none"

    for row in ips:
        relevant = [r for r in routing if overlaps(row, r)]
        row["routes"] = [
            r["prefix"] for r in relevant if (row["start"], row["end"]) == (r["start"], r["end"])
        ]
        row["more_routes"] = [
            r["prefix"] for r in relevant if contained(r, row) and r["prefix"] not in row["routes"]
        ]
        row["covering_routes"] = [
            r["prefix"] for r in relevant if contained(row, r) and r["prefix"] not in row["routes"]
        ]
        row["origins"] = sorted({origin for r in relevant for origin in r["origins"]})
        row["bgp_state"] = (
            "exact"
            if row["routes"]
            else "mixed"
            if row["more_routes"] and row["covering_routes"]
            else "more"
            if row["more_routes"]
            else "covering"
            if row["covering_routes"]
            else "none"
        )
    existing = {(r["family"], r["start"], r["end"]) for r in ips}
    for route in routing:
        if (route["family"], route["start"], route["end"]) in existing:
            continue
        parents = [r for r in ips if contained(route, r)]
        if not parents:
            continue
        parent = min(parents, key=lambda r: r["end"] - r["start"])
        linked = [f["id"] for f in findings if f["announced_prefix"] == route["prefix"]]
        resources.append(
            {
                "id": "bgp:" + route["prefix"],
                "type": "bgp",
                "family": route["family"],
                "resource": route["prefix"],
                "status": "NO-EXACT-INETNUM",
                "netname": "BGP without an exact inetnum/inet6num",
                "org_handles": parent["org_handles"][:],
                "org_names": parent["org_names"][:],
                "org_source": "containing_object",
                "org_source_id": parent["id"],
                "relations": ["routing_observation"],
                "parent_id": parent["id"],
                "level": parent["level"] + 1,
                "level1_id": parent["level1_id"],
                "findings": [],
                "context_findings": linked,
                "children_count": 0,
                "bgp_state": "exact",
                "origins": route["origins"],
                "routes": [route["prefix"]],
                "more_routes": [],
                "covering_routes": [],
                "ripe_url": ripe_url(route["prefix"]),
                "assignment_size": "",
                "contacts": {},
            }
        )
        parent["children_count"] += 1
    for row in resources:
        row["finding_count"] = len(row["findings"])
        row["context_finding_count"] = len(row.get("context_findings", []))
        row["descendant_bad"] = 0
    by_id = {r["id"]: r for r in resources}
    for row in sorted(resources, key=lambda r: r["level"], reverse=True):
        if row["parent_id"]:
            by_id[row["parent_id"]]["descendant_bad"] += row["descendant_bad"] + bool(
                row["findings"]
            )
    counts = collections.Counter(r["family"] for r in resources if r["type"] != "bgp")
    return findings, {
        "ipv4": counts["ipv4"],
        "ipv6": counts["ipv6"],
        "asn": counts["asn"],
        "objects": sum(counts.values()),
        "routes": len(routes),
        "findings": len(findings),
        "errors": sum(f["severity"] == "error" for f in findings),
        "warnings": sum(f["severity"] == "warning" for f in findings),
        "affected_resources": len({f["resource_id"] for f in findings}),
        "allocation_announcement_checks": checks,
        "status_counts": dict(
            collections.Counter(r["status"] for r in resources if r["type"] != "bgp")
        ),
    }
