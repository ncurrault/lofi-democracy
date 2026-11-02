#!/usr/bin/env python3
"""Resolve SF Elections camera streams to ipcamlive HLS (.m3u8) URLs.

The SF Elections observation page fills its camera <select> from a JSON
endpoint (api/cameras.php) shaped like:

    {"ballot-scanning": [{"id": 74, "name": "Camera 07",
                          "link": "https://g1.ipcamlive.com/player/player.php?alias=..."}], ...}

Each link carries an ipcamlive camera alias. ipcamlive's
getcamerastreamstate.php turns an alias into the stream server address and
current stream id, from which the HLS URL is built the same way the official
player does: {address}streams/{streamid}/stream.m3u8, with streams_storage/
or streams_timeshift/ in place of streams/ when the camera has storage or
timeshift enabled.

By default the result is written to streams.json, which index.html loads at
startup. Stream ids change over time, so rerun this whenever the cameras stop
playing.

sfelections.org sits behind CloudFront, which may reject non-browser
requests with a 403. If so, save the JSON from your browser (open the
cameras.php URL below and save the page) and pass the file instead:

    python3 scripts/fetch_streams.py --cameras-json cameras.json

Uses only the standard library.
"""

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CAMERAS_URL = "https://sfelections.org/tools/election_video_stream/api/cameras.php"
STREAM_STATE_URL = "https://g1.ipcamlive.com/player/getcamerastreamstate.php"
USER_AGENT = "lofi-democracy-stream-resolver/1.0"
STREAMS_PATH = Path(__file__).resolve().parent.parent / "streams.json"


def fetch(url):
    # ipcamlive rejects urllib's default "Python-urllib" User-Agent.
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def load_cameras(args):
    """Returns a list of {"activity", "name", "link"} dicts."""
    if args.cameras_json:
        with open(args.cameras_json, encoding="utf-8") as f:
            data = json.load(f)
    else:
        try:
            data = json.loads(fetch(CAMERAS_URL))
        except urllib.error.HTTPError as e:
            sys.exit(
                f"Fetching {CAMERAS_URL} failed ({e.code}). Save it from a browser "
                "and rerun with --cameras-json FILE."
            )

    return [
        {"activity": activity, "name": cam["name"], "link": cam["link"]}
        for activity, cams in data.items()
        for cam in cams
    ]


def humanize(activity_key):
    text = activity_key.replace("-", " ")
    text = re.sub(r"\bvote by mail\b", "Vote-by-Mail", text)
    return text[0].upper() + text[1:]


def resolve(camera):
    """Adds "alias" and "m3u8" (None if the stream is unavailable)."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(camera["link"]).query)
    alias = query["alias"][0]
    state = json.loads(fetch(f"{STREAM_STATE_URL}?alias={alias}"))["details"]

    m3u8 = None
    if state.get("streamavailable") == "1" and state.get("streamid"):
        address = state["address"].replace("http://", "https://", 1)
        # Mirrors the player's choice of directory; storage-enabled cameras
        # 404 under plain streams/.
        if state.get("storageenabled") == "1":
            directory = "streams_storage"
        elif state.get("timeshiftenabled") == "1":
            directory = "streams_timeshift"
        else:
            directory = "streams"
        m3u8 = f"{address}{directory}/{state['streamid']}/stream.m3u8"
    return dict(camera, alias=alias, m3u8=m3u8)


def is_playing(m3u8):
    # ipcamlive can report a stream as available while its playlist 404s
    # (e.g. an idle camera overnight).
    try:
        fetch(m3u8)
        return True
    except urllib.error.HTTPError:
        return False


def camera_number(name):
    match = re.search(r"\d+", name)
    return int(match.group()) if match else None


def page_record(cam):
    """The {"id", "label", "url"} shape index.html passes to addCamera."""
    num = camera_number(cam["name"])
    label = f"{num:02d}" if num is not None else cam["name"]
    label += f" ({humanize(cam['activity'])})"
    return {"id": num, "label": label, "url": cam["m3u8"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cameras-json", metavar="FILE", help="saved api/cameras.php response")
    parser.add_argument(
        "--format",
        choices=["page", "json", "urls"],
        default="page",
        help="page: write streams.json for index.html (default); json: print full records; urls: print bare m3u8 URLs",
    )
    parser.add_argument(
        "--output",
        metavar="FILE",
        type=Path,
        default=STREAMS_PATH,
        help=f"where --format page writes (default: {STREAMS_PATH.name} next to index.html)",
    )
    parser.add_argument("--check", action="store_true", help="drop streams whose playlist does not load")
    args = parser.parse_args()

    # A camera can appear under several activities; resolve each alias once.
    cameras, seen = [], set()
    for cam in load_cameras(args):
        if cam["link"] not in seen:
            seen.add(cam["link"])
            cameras.append(cam)

    with ThreadPoolExecutor(max_workers=8) as pool:
        resolved = list(pool.map(resolve, cameras))
        if args.check:
            playing = pool.map(lambda c: c["m3u8"] is not None and is_playing(c["m3u8"]), resolved)
            for cam, ok in zip(resolved, list(playing)):
                if not ok:
                    cam["m3u8"] = None

    available = []
    for cam in resolved:
        if cam["m3u8"]:
            available.append(cam)
        else:
            print(f"warning: {cam['name']} (alias {cam['alias']}) has no live stream", file=sys.stderr)

    if args.format == "json":
        print(json.dumps(resolved, indent=2))
    elif args.format == "urls":
        print("\n".join(cam["m3u8"] for cam in available))
    else:
        records = [page_record(cam) for cam in sorted(available, key=lambda cam: cam["name"])]
        args.output.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(records)} cameras to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
