import asyncio
import datetime as dt
import ipaddress
import re
import time
import urllib.parse

import httpx

from .models import ORG, iso, moment

CATALOG_URL = "https://ftp.ripe.net/ripe/stats/membership/alloclist.txt"
DB = "https://rest.db.ripe.net/search.json"
STAT = "https://stat.ripe.net/data/"


class SourceError(Exception):
    pass


class UnknownRegid(Exception):
    pass


def catalog_entry(text, regid):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == regid and line and not line[0].isspace():
            block = []
            for value in lines[i + 1 :]:
                if value and not value[0].isspace() and re.fullmatch(r"[a-z]{2}\.[\w.-]+", value):
                    break
                if value.strip():
                    block.append(value.strip())
            prefixes = []
            for value in block:
                parts = value.split()
                if len(parts) >= 2 and re.fullmatch(r"\d{8}", parts[0]):
                    try:
                        prefixes.append(str(ipaddress.ip_network(parts[1])))
                    except ValueError:
                        pass
            return {
                "regid": regid,
                "holder": block[0] if block else regid,
                "prefixes": sorted(set(prefixes)),
                "catalog_url": CATALOG_URL,
            }
    raise UnknownRegid(
        "regID was not found in the public RIPE catalogue. Check the spelling; catalogue updates may be delayed."
    )


class RipeClient:
    def __init__(self, limit=6):
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(40, connect=10),
            limits=httpx.Limits(max_connections=limit),
            headers={"User-Agent": "ARC-LIR-Analyzer/1.0", "Accept": "application/json"},
            follow_redirects=False,
        )
        self.gate = asyncio.Semaphore(limit)
        self.records = []
        self.catalog = None
        self.catalog_time = 0
        self.catalog_lock = asyncio.Lock()

    async def close(self):
        await self.client.aclose()

    async def get(self, url, *, empty_ok=False, text=False, headers=None):
        for attempt in range(3):
            try:
                async with self.gate:
                    response = await self.client.get(url, headers=headers)
                if response.status_code == 404 and empty_ok:
                    doc = response.json()
                    errors = doc.get("errormessages", {}).get("errormessage", [])
                    if any("101" in e.get("text", "") for e in errors):
                        return {"objects": {"object": []}}
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < 2:
                        await asyncio.sleep(
                            min(float(response.headers.get("Retry-After", 2**attempt)), 15)
                        )
                        continue
                response.raise_for_status()
                doc = response.text if text else response.json()
                if not text and (
                    doc.get("status") not in (None, "ok")
                    or doc.get("errormessages", {}).get("errormessage")
                ):
                    raise SourceError("RIPE returned a data error")
                return doc
            except (httpx.HTTPError, ValueError) as error:
                if attempt < 2 and not isinstance(error, httpx.HTTPStatusError):
                    await asyncio.sleep(2**attempt)
                    continue
                code = (
                    error.response.status_code
                    if isinstance(error, httpx.HTTPStatusError)
                    else "network"
                )
                raise SourceError(
                    f"Source {urllib.parse.urlsplit(url).hostname} is unavailable ({code})"
                ) from None
        raise SourceError("RIPE source is temporarily unavailable")

    async def resolve(self, regid):
        async with self.catalog_lock:
            if self.catalog is None or time.time() - self.catalog_time >= 3600:
                self.catalog = await self.get(CATALOG_URL, text=True)
                self.catalog_time = time.time()
            entry = catalog_entry(self.catalog, regid)
            entry["catalog_retrieved_at"] = iso(
                dt.datetime.fromtimestamp(self.catalog_time, dt.timezone.utc)
            )
            return entry

    @staticmethod
    def search_url(query, typ=None, flag=None, inverse=None):
        pairs = [("source", "ripe"), ("query-string", query), ("flags", "no-referenced")]
        for t in typ or ["inetnum", "inet6num", "aut-num"]:
            pairs.append(("type-filter", t))
        if flag:
            pairs.append(("flags", flag))
        if inverse:
            pairs.append(("inverse-attribute", inverse))
        return DB + "?" + urllib.parse.urlencode(pairs)

    async def objects(self, query, typ=None, flag=None, inverse=None):
        url = self.search_url(query, typ, flag, inverse)
        doc = await self.get(url, empty_ok=True)
        return doc.get("objects", {}).get("object", []), {"url": url, "retrieved_at": iso()}

    async def routing(self, resource, reference=None):
        params = {"resource": resource, "min_peers_seeing": 1}
        if reference:
            params["timestamp"] = reference
        url = STAT + "routing-status/data.json?" + urllib.parse.urlencode(params)
        doc = await self.get(url)
        data = doc["data"]
        if reference and moment(data["query_time"]) != moment(reference):
            raise SourceError("RIPE RIS returned a different BGP snapshot time")
        return data, {"url": url, "retrieved_at": iso(), "snapshot": data["query_time"] + "Z"}

    async def activity(self, asn, reference, cutoff):
        url = (
            STAT
            + "announced-prefixes/data.json?"
            + urllib.parse.urlencode(
                {"resource": asn, "starttime": cutoff, "endtime": reference, "min_peers_seeing": 1}
            )
        )
        doc = await self.get(url)
        data = doc["data"]
        if moment(data["query_starttime"]) != moment(cutoff) or moment(
            data["query_endtime"]
        ) != moment(reference):
            raise SourceError("RIPE RIS returned a different ASN activity window")
        return data, {"url": url, "retrieved_at": iso(), "snapshot": reference}

    async def organisation(self, handle):
        if not ORG.fullmatch(handle):
            raise SourceError("Invalid ORG handle in RIPE response")
        url = "https://rest.db.ripe.net/ripe/organisation/" + handle + ".json"
        doc = await self.get(url, empty_ok=True)
        for obj in doc.get("objects", {}).get("object", []):
            attrs = {a["name"]: a["value"] for a in obj["attributes"]["attribute"]}
            if attrs.get("organisation") == handle:
                return attrs.get("org-name", ""), {"url": url, "retrieved_at": iso()}
        return "", {"url": url, "retrieved_at": iso()}

    async def portal(self, regid, key):
        url = "https://lirportal.ripe.net/myresources/v1/resources?format=JSON"
        doc = await self.get(url, headers={"ncc-api-authorization": key})
        if str(doc.get("regid", "")).lower() != regid:
            raise SourceError("LIR Portal key belongs to a different regID")
        prefixes, asns = set(), set()
        for value in doc.values():
            if not isinstance(value, list):
                continue
            for row in value:
                if not isinstance(row, dict):
                    continue
                if row.get("prefix"):
                    prefixes.add(str(ipaddress.ip_network(row["prefix"])))
                for field in ("asn", "asNumber", "autonomousSystemNumber"):
                    if field in row and re.fullmatch(r"(?:AS)?\d+", str(row[field])):
                        asns.add("AS" + str(row[field]).removeprefix("AS"))
        return sorted(prefixes), sorted(asns), {"url": url, "retrieved_at": iso()}
