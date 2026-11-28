"""Explicit, opt-in updater for official GitHub release assets."""
import hashlib
import json
import re
import tempfile
import urllib.request
from pathlib import Path
from wattcycle_version import VERSION

REPO="SecondShiftLabs1/wattcycle-battery-utility"
ASSET="WattCycle-Setup.exe"
def version_key(v):
    match=re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-beta(?:\.(\d+))?)?",v)
    if not match:return None
    a,b,c,beta=match.groups()
    return (int(a),int(b),int(c),1 if "-beta" not in v else 0,int(beta or 0))
def request(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"WattCycle-Utility","Accept":"application/vnd.github+json"}),timeout=20)
def find_update():
    with request(f"https://api.github.com/repos/{REPO}/releases?per_page=30") as response:
        releases=json.load(response)
    candidates=[]
    for release in releases:
        tag=release.get("tag_name","");key=version_key(tag)
        if release.get("draft") or key is None or key<=version_key(VERSION):continue
        assets={a["name"]:a for a in release.get("assets",[])}
        if ASSET in assets and ASSET+".sha256" in assets:candidates.append((key,tag,assets))
    return max(candidates,key=lambda x:x[0]) if candidates else None
def download_update(update):
    _,tag,assets=update
    def url(name):
        # Only allow assets from this repository's official release path.
        expected=f"https://github.com/{REPO}/releases/download/{tag}/{name}"
        if assets[name]["browser_download_url"]!=expected:raise ValueError("Unexpected release asset URL")
        return expected
    with request(url(ASSET+".sha256")) as response:
        expected=response.read(4096).decode("ascii").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}",expected):raise ValueError("Invalid release checksum")
    directory=Path(tempfile.mkdtemp(prefix="WattCycleUpdate-"))
    target=directory/ASSET
    try:
        digest=hashlib.sha256()
        with request(url(ASSET)) as response,target.open("wb") as f:
            total=0
            while True:
                chunk=response.read(1024*1024)
                if not chunk:break
                total+=len(chunk)
                if total>300*1024*1024:raise ValueError("Installer exceeds size limit")
                digest.update(chunk);f.write(chunk)
        if digest.hexdigest()!=expected:raise ValueError("Installer checksum mismatch")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise
