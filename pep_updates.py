"""Look up public Electrum-PEP release metadata without sending wallet state."""
import json
import re
from urllib.request import Request, urlopen

from pep_version import VERSION


API_URL = "https://api.github.com/repos/AltForger/Electrum-PEP/releases/latest"
RELEASES = "https://github.com/AltForger/Electrum-PEP/releases/tag/"
VERSION_TAG = re.compile(r"v(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})")


def available_release(data, current=VERSION):
    if not isinstance(data, dict) or data.get("draft") is not False or data.get("prerelease") is not False:
        return None
    tag = data.get("tag_name")
    match = VERSION_TAG.fullmatch(tag) if isinstance(tag, str) else None
    if match and tuple(map(int, match.groups())) > tuple(map(int, current.split("."))):
        return tag
    return None


def check_latest():
    request = Request(API_URL, headers={"Accept": "application/vnd.github+json",
                                        "User-Agent": f"Electrum-PEP/{VERSION}"})
    with urlopen(request, timeout=8) as response:
        payload = response.read(1_048_577)
    if len(payload) > 1_048_576:
        raise ValueError("Release metadata too large")
    return available_release(json.loads(payload))
