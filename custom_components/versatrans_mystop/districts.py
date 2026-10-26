"""Public school district directory, with an offline setup fallback."""
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

DIRECTORY_URL = "https://mystopclientlistapi.azurewebsites.net/api/ClientList/getall"


def parse_directory(payload):
    """Keep every usable directory record; retain HTTP entries for explanation."""
    if not isinstance(payload, dict) or not isinstance(payload.get("Clients"), list):
        raise ValueError("Invalid district directory")
    districts = {}
    for row in payload["Clients"]:
        if not isinstance(row, dict):
            continue
        fields = ("Account", "Serial", "ServiceUrl")
        if not all(isinstance(row.get(k), str) and row[k].strip() for k in fields):
            continue
        url = row["ServiceUrl"].strip().rstrip("/") + "/"
        try:
            parsed = urlsplit(url)
            valid = (parsed.scheme in ("https", "http") and parsed.hostname
                     and not parsed.username and not parsed.password
                     and not parsed.query and not parsed.fragment)
            parsed.port
        except ValueError:
            continue
        if not valid:
            continue
        url = parsed.geturl()
        key = str(row.get("RecordID") or row["Serial"])
        place = ", ".join(str(row.get(k) or "").strip() for k in ("City", "State", "Country") if row.get(k))
        label = row["Account"].strip() + (f" ({place})" if place else "")
        if parsed.scheme != "https":
            label += " [HTTP only]"
        districts[key] = {"district_id": key, "district_name": row["Account"].strip(),
                          "url": url, "school_guid": row["Serial"].strip(), "label": label}
    if not districts:
        raise ValueError("Empty district directory")
    return districts


def bundled_directory():
    return parse_directory(json.loads(Path(__file__).with_name("districts.json").read_text(encoding="utf-8")))


async def load_directory(hass):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    try:
        async with async_get_clientsession(hass).get(
            DIRECTORY_URL, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False
        ) as response:
            if response.status != 200:
                raise ValueError("Directory unavailable")
            return parse_directory(await response.json()), False
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        return await hass.async_add_executor_job(bundled_directory), True
