import asyncio
import ipaddress

from .models import cutoff, hierarchy, iso, moment, parse_object
from .rules import analyze
from .sources import SourceError


class AnalysisLimit(Exception):
    pass


class Analyzer:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    async def run(self, regid, emit):
        sources, warnings, objects = [], [], {}
        started = iso()

        async def progress(stage, message, done=0, total=None):
            await emit(
                {
                    "stage": stage,
                    "message": message,
                    "done": done,
                    "total": total,
                    "objects": len(objects),
                    "requests": len(sources),
                }
            )

        def merge(documents, relation):
            for doc in documents:
                row = parse_object(doc, relation)
                if row is None:
                    continue
                if row["id"] in objects:
                    objects[row["id"]]["relations"] = sorted(
                        set(objects[row["id"]]["relations"] + [relation])
                    )
                else:
                    if len(objects) >= self.settings.max_objects:
                        raise AnalysisLimit("Configured resource limit reached")
                    objects[row["id"]] = row

        await progress("resolve", "Looking up regID in the official RIPE catalogue")
        entry = await self.client.resolve(regid)
        sources.append({"url": entry["catalog_url"], "retrieved_at": entry["catalog_retrieved_at"]})
        roots = entry["prefixes"][:]
        portal_asns = []
        mode = "public"
        if regid in self.settings.portal_keys:
            roots, portal_asns, src = await self.client.portal(
                regid, self.settings.portal_keys[regid]
            )
            sources.append(src)
            mode = "portal"
        await progress("resolve", f"Found {entry['holder']}", 1, 1)
        await progress("resolve", "Resolving root RIPE objects", 0, len(roots))
        completed = 0

        async def exact(prefix):
            nonlocal completed
            typ = "inet6num" if ":" in prefix else "inetnum"
            try:
                docs, src = await self.client.objects(prefix, [typ], "exact")
                sources.append(src)
                merge(docs, "allocation")
                if not docs:
                    warnings.append(
                        {
                            "code": "missing_registry_root",
                            "message": f"Catalogue object {prefix} was not found in RIPE Database.",
                        }
                    )
            except SourceError as error:
                warnings.append(
                    {"code": "registration_source_failed", "message": f"{prefix}: {error}"}
                )
            completed += 1
            await progress(
                "resolve", f"Root networks: {completed} of {len(roots)}", completed, len(roots)
            )

        await asyncio.gather(*(exact(prefix) for prefix in roots))
        owner_orgs = sorted({org for row in objects.values() for org in row["org_handles"]})

        await progress(
            "relations", "Discovering own and sponsored ORG resources", 0, len(owner_orgs) * 2
        )
        completed = 0

        async def related(org, relation):
            nonlocal completed
            try:
                docs, src = await self.client.objects(org, inverse=relation)
                sources.append(src)
                # The allocation catalogue is regID-specific. An ORG may have several LIRs:
                # don't silently annex the other accounts' allocations.
                docs = [
                    d
                    for d in docs
                    if d["type"] == "aut-num"
                    or any(
                        a["name"] == "status"
                        and a["value"]
                        not in (
                            "ALLOCATED PA",
                            "ALLOCATED-BY-RIR",
                            "ALLOCATED UNSPECIFIED",
                            "ALLOCATED PI",
                            "ALLOCATED-ASSIGNED PA",
                        )
                        for a in d["attributes"]["attribute"]
                    )
                ]
                merge(docs, relation)
            except SourceError as error:
                warnings.append({"code": "relations_source_failed", "message": f"{org}: {error}"})
            completed += 1
            await progress(
                "relations", "ORG relations: " + str(completed), completed, len(owner_orgs) * 2
            )

        await asyncio.gather(
            *(
                related(org, relation)
                for org in owner_orgs
                for relation in ("org", "sponsoring-org")
            )
        )
        for asn in portal_asns:
            docs, src = await self.client.objects(asn, ["aut-num"], "exact")
            sources.append(src)
            merge(docs, "portal")
        if not owner_orgs:
            warnings.append(
                {
                    "code": "organisation_unresolved",
                    "message": "Could not resolve the ORG; related ASN and PI resources may be missing from public discovery.",
                }
            )
        # Add PI/sponsored roots, preserving non-CIDR ranges as genuine objects.
        ip_rows = [r for r in objects.values() if r["family"] != "asn"]
        root_networks = [ipaddress.ip_network(p) for p in roots]
        for row in ip_rows:
            if not any(
                n.version == (6 if row["family"] == "ipv6" else 4)
                and int(n.network_address) <= row["start"]
                and row["end"] <= int(n.broadcast_address)
                for n in root_networks
            ):
                address_type = (
                    ipaddress.IPv6Address if row["family"] == "ipv6" else ipaddress.IPv4Address
                )
                root_networks.extend(
                    ipaddress.summarize_address_range(
                        address_type(row["start"]), address_type(row["end"])
                    )
                )
        roots = [
            str(n)
            for n in ipaddress.collapse_addresses([n for n in root_networks if n.version == 4])
        ] + [
            str(n)
            for n in ipaddress.collapse_addresses([n for n in root_networks if n.version == 6])
        ]
        await progress("registrations", "Loading child inetnum and inet6num objects", 0, len(roots))
        completed = 0

        async def descendants(prefix):
            nonlocal completed
            try:
                docs, src = await self.client.objects(
                    prefix, ["inet6num" if ":" in prefix else "inetnum"], "all-more"
                )
                sources.append(src)
                merge(docs, "more-specific")
            except SourceError as error:
                warnings.append(
                    {"code": "descendants_source_failed", "message": f"{prefix}: {error}"}
                )
            completed += 1
            await progress(
                "registrations",
                "Networks and child records: " + str(len(objects)),
                completed,
                len(roots),
            )

        await asyncio.gather(*(descendants(prefix) for prefix in roots))
        if not objects:
            warnings.append(
                {
                    "code": "no_public_resources",
                    "message": "No resources could be linked to this regID in public sources.",
                }
            )
        resources = list(objects.values())
        hierarchy(resources)
        handles = sorted({h for r in resources for h in r["org_handles"]})
        await progress("organisations", "Loading organisation names", 0, len(handles))
        names = {}
        completed = 0

        async def name(handle):
            nonlocal completed
            try:
                value, src = await self.client.organisation(handle)
                names[handle] = value
                sources.append(src)
            except SourceError as error:
                warnings.append({"code": "org_source_failed", "message": f"{handle}: {error}"})
            completed += 1
            await progress(
                "organisations",
                f"Organisations: {completed} of {len(handles)}",
                completed,
                len(handles),
            )

        await asyncio.gather(*(name(h) for h in handles))
        for row in resources:
            row["org_names"] = [names.get(h, "") for h in row["org_handles"]]

        await progress(
            "routing", "Loading the RIPE RIS snapshot; splitting large queries into subnets"
        )
        routes, reference, query_count = {}, None, 0
        pending = [ipaddress.ip_network(p) for p in roots]
        while pending:
            if query_count + len(pending) > self.settings.max_routing_queries:
                raise SourceError("Too many BGP queries. Increase the server limit.")
            # First query chooses a single RIS snapshot for every following root/child.
            batch = pending[:1] if reference is None else pending
            pending = pending[len(batch) :]

            async def query(net, snapshot=reference):
                try:
                    data, src = await self.client.routing(str(net), snapshot)
                    sources.append(src)
                    return net, data
                except SourceError as error:
                    warnings.append({"code": "routing_source_failed", "message": f"{net}: {error}"})
                    return net, None

            for net, data in await asyncio.gather(*(query(n) for n in batch)):
                query_count += 1
                if data is None:
                    continue
                reference = reference or iso(moment(data["query_time"]))
                entries = data.get("more_specifics", []) + data.get("less_specifics", [])
                entries += [
                    {"prefix": str(net), "origin": o["origin"]} for o in data.get("origins", [])
                ]
                for route in entries:
                    prefix = str(ipaddress.ip_network(route["prefix"]))
                    routes.setdefault(prefix, set()).add("AS" + str(route["origin"]))
                if len(data.get("more_specifics", [])) >= 50:
                    if net.prefixlen == net.max_prefixlen:
                        raise SourceError("RIPE RIS result limit at maximum prefix length")
                    pending.extend(net.subnets())
            await progress(
                "routing",
                f"BGP queries: {query_count}; prefixes: {len(routes)}; queued: {len(pending)}",
                query_count,
                query_count + len(pending),
            )
        asns = [r for r in resources if r["family"] == "asn"]
        if reference is None and asns:
            data, src = await self.client.routing(asns[0]["resource"])
            reference = iso(moment(data["query_time"]))
            sources.append(src)
        reference = reference or iso()
        activity = {}
        completed = 0
        await progress(
            "asn", "Checking ASN origin announcements over three calendar months", 0, len(asns)
        )

        async def check_asn(row):
            nonlocal completed
            try:
                (status, src1), (recent, src2) = await asyncio.gather(
                    self.client.routing(row["resource"], reference),
                    self.client.activity(
                        row["resource"], reference, iso(cutoff(moment(reference)))
                    ),
                )
                sources.extend([src1, src2])
                last = status.get("last_seen", {})
                if last and str(last.get("origin")) != row["resource"][2:]:
                    raise SourceError("Origin ASN mismatch in last_seen response")
                activity[row["resource"]] = {
                    "recent": bool(recent["prefixes"]),
                    "last_seen": iso(moment(last["time"])) if last.get("time") else None,
                    "last_seen_prefix": last.get("prefix"),
                    "current_origins": sum(
                        v.get("prefixes", 0) for v in status.get("announced_space", {}).values()
                    ),
                }
            except SourceError as error:
                warnings.append(
                    {"code": "asn_activity_failed", "message": f"{row['resource']}: {error}"}
                )
            completed += 1
            await progress("asn", f"ASN: {completed} of {len(asns)}", completed, len(asns))

        await asyncio.gather(*(check_asn(r) for r in asns))
        await progress("analysis", "Checking registrations, assignments, statuses and nesting")
        route_rows = [
            {"prefix": p, "origins": sorted(origins), "snapshot": reference}
            for p, origins in sorted(
                routes.items(),
                key=lambda p: (
                    ipaddress.ip_network(p[0]).version,
                    int(ipaddress.ip_network(p[0]).network_address),
                    ipaddress.ip_network(p[0]).prefixlen,
                ),
            )
        ]
        findings, summary = await asyncio.to_thread(
            analyze, resources, route_rows, activity, reference
        )
        for row in resources:
            row.pop("start", None)
            row.pop("end", None)
        await progress("analysis", "Checks complete", 1, 1)
        return {
            "schema_version": 1,
            "regid": regid,
            "holder": entry["holder"],
            "org_handles": owner_orgs,
            "started_at": started,
            "completed_at": iso(),
            "bgp_snapshot": reference,
            "coverage": {
                "mode": mode,
                "complete": not warnings,
                "scope": "Public allocation catalogue and org / sponsoring-org links in RIPE Database"
                if mode == "public"
                else "LIR Portal resources and public ORG relations",
                "note": "PI and ASN links are discovered through ORG. One organisation may belong to multiple LIRs; a regID alone does not provide access to private portal data.",
                "warnings": warnings,
            },
            "summary": summary,
            "resources": resources,
            "routes": route_rows,
            "findings": findings,
            "sources": sources,
            "manual_checks": [
                "Contracts and assignment justification — RIPE-826 §6.3, §7.",
                "Assignment statistics within IPv4 AGGREGATED-BY-LIR — §6.2.",
                "New /24 allocation limit and allocation/transfer history — §5.1; existing larger allocations are not flagged automatically.",
            ],
        }
