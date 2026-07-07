#!/usr/bin/env python3
"""Verify that the local exe, git tag, and GitHub release asset match."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "campus_auto_login.exe"
ASSET_NAME = "campus_auto_login.exe"


def run(args):
    return subprocess.run(
        args,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upstream_ref():
    try:
        return run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    except subprocess.CalledProcessError:
        return "origin/master"


def remote_tag_commit(tag):
    refs = run([
        "git", "ls-remote", "--tags", "origin",
        "refs/tags/{0}".format(tag),
        "refs/tags/{0}^{{}}".format(tag),
    ])
    peeled = None
    direct = None
    for line in refs.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        sha, ref = parts
        if ref.endswith("^{}"):
            peeled = sha
        elif ref == "refs/tags/{0}".format(tag):
            direct = sha
    return peeled or direct or ""


def is_latest_release(tag):
    releases_raw = run(["gh", "release", "list", "--limit", "5", "--json", "tagName,isLatest"])
    releases = json.loads(releases_raw)
    return any(item.get("tagName") == tag and item.get("isLatest") is True for item in releases)


def require(condition, message):
    if not condition:
        raise SystemExit("ERROR: " + message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="Release tag, for example v1.4.5")
    parser.add_argument("--skip-remote", action="store_true",
                        help="Only verify local git/tag/exe state.")
    args = parser.parse_args()

    require(EXE.exists(), "missing exe: {0}".format(EXE))
    head = run(["git", "rev-parse", "HEAD"])
    upstream = upstream_ref()
    origin = run(["git", "rev-parse", upstream])
    status = run(["git", "status", "--short"])
    tag_commit = run(["git", "rev-parse", "{0}^{{commit}}".format(args.tag)])
    local_hash = sha256_file(EXE)
    local_size = EXE.stat().st_size

    require(head == origin, "HEAD does not match {0}".format(upstream))
    require(head == tag_commit, "{0} does not point at HEAD".format(args.tag))
    require(status == "", "working tree is not clean")

    print("local commit: {0}".format(head))
    print("local exe: {0} bytes sha256:{1}".format(local_size, local_hash))

    if args.skip_remote:
        return 0

    remote_tag = remote_tag_commit(args.tag)
    require(head == remote_tag, "origin tag {0} does not point at HEAD".format(args.tag))

    release_raw = run([
        "gh", "release", "view", args.tag,
        "--json", "tagName,assets,targetCommitish,url",
    ])
    release = json.loads(release_raw)
    require(release.get("tagName") == args.tag, "release tag mismatch")
    require(is_latest_release(args.tag), "{0} is not marked Latest".format(args.tag))
    assets = release.get("assets") or []
    matches = [a for a in assets if a.get("name") == ASSET_NAME]
    require(matches, "release asset missing: {0}".format(ASSET_NAME))
    asset = matches[0]
    digest = asset.get("digest") or ""
    if digest.startswith("sha256:"):
        require(digest[7:].lower() == local_hash, "release asset digest differs")
    require(int(asset.get("size") or 0) == local_size, "release asset size differs")
    print("release: {0}".format(release.get("url")))
    print("release asset matches local exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
