import calendar
import collections
import datetime as dt
import ipaddress
import re
import urllib.parse

REGID = re.compile(r"[a-z]{2}\.[a-z0-9][a-z0-9._-]{0,75}\Z")
ORG = re.compile(r"ORG-[A-Z0-9]+-RIPE\Z")


def regid(value):
    value = value.strip().lower()
    if not REGID.fullmatch(value):
        raise ValueError("Enter a regID such as ru.ecode")
    return value


def iso(value=None):
    return (
        (value or dt.datetime.now(dt.timezone.utc))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def moment(value):
    parsed = dt.datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=dt.timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(dt.timezone.utc)
    )


def cutoff(value):
    index = value.year * 12 + value.month - 1 - 3
    year, month = divmod(index, 12)
    return value.replace(
        year=year, month=month + 1, day=min(value.day, calendar.monthrange(year, month + 1)[1])
    )


def ripe_url(value):
    return "https://apps.db.ripe.net/db-web-ui/query?" + urllib.parse.urlencode(
        {"searchtext": value}
    )


def bounds(value):
    if " - " in value:
        start, end = (ipaddress.ip_address(p.strip()) for p in value.split(" - "))
        if start.version != end.version or int(start) > int(end):
            raise ValueError("Invalid address range")
        nets = list(ipaddress.summarize_address_range(start, end))
        return (
            int(start),
            int(end),
            start.version,
            str(nets[0]) if len(nets) == 1 else f"{start} - {end}",
        )
    net = ipaddress.ip_network(value, strict=False)
    return int(net.network_address), int(net.broadcast_address), net.version, str(net)


def contained(a, b):
    return a["family"] == b["family"] and b["start"] <= a["start"] <= a["end"] <= b["end"]


def overlaps(a, b):
    return a["family"] == b["family"] and a["start"] <= b["end"] and b["start"] <= a["end"]


def parse_object(obj, relation):
    attrs = collections.defaultdict(list)
    for a in obj["attributes"]["attribute"]:
        attrs[a["name"]].append(a["value"])
    typ = obj["type"]
    if typ not in ("inetnum", "inet6num", "aut-num"):
        return None
    value = attrs[typ][0]
    row = {
        "id": f"{typ}:{value}",
        "type": typ,
        "family": "asn",
        "resource": value,
        "status": ";".join(attrs["status"]),
        "netname": ";".join(attrs["netname"] or attrs["as-name"]),
        "org_handles": attrs["org"],
        "direct_org_handles": attrs["org"][:],
        "org_source": "direct" if attrs["org"] else "missing",
        "org_names": [],
        "assignment_size": ";".join(attrs["assignment-size"]),
        "created": next(iter(attrs["created"]), None),
        "sponsoring_org": attrs["sponsoring-org"],
        "relations": [relation],
        "ripe_url": ripe_url(value),
        "parent_id": None,
        "level": 0,
        "level1_id": None,
        "findings": [],
        "children_count": 0,
        "bgp_state": "unknown",
        "origins": [],
        "routes": [],
        "covering_routes": [],
        "more_routes": [],
        "contacts": {k: attrs[k] for k in ("admin-c", "tech-c", "abuse-c", "mnt-by") if attrs[k]},
    }
    if typ != "aut-num":
        start, end, version, canonical = bounds(value)
        row.update(start=start, end=end, resource=canonical, family=f"ipv{version}", original=value)
    return row


def hierarchy(resources):
    ips = sorted(
        (r for r in resources if r["family"] != "asn"),
        key=lambda r: (r["family"], r["start"], -r["end"]),
    )
    for row in ips:
        row["children_count"] = 0
    for r in ips:
        parents = [
            p
            for p in ips
            if p["id"] != r["id"]
            and contained(r, p)
            and (r["start"], r["end"]) != (p["start"], p["end"])
        ]
        parent = min(parents, key=lambda p: p["end"] - p["start"]) if parents else None
        r["parent_id"] = parent["id"] if parent else None
        r["level"] = parent["level"] + 1 if parent else 0
        r["level1_id"] = r["id"] if r["level"] == 1 else parent.get("level1_id") if parent else None
        r["allocation_depth"] = (parent.get("allocation_depth", 0) if parent else 0) + int(
            r["status"] == "ALLOCATED-BY-LIR"
        )
        if parent:
            parent["children_count"] += 1
            if not r["org_handles"]:
                r["org_handles"] = parent["org_handles"][:]
                r["org_source"] = "inherited"
                r["org_source_id"] = parent.get("org_source_id", parent["id"])
    return ips
