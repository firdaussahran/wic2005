#!/usr/bin/env python3
"""Week 7 lab, Steps 5 and 6: read EVERY device from the lab API, however it misbehaves.

Run it in the client container:
    export LAB_USERNAME=netops LAB_PASSWORD=netops-pass   # in the environment, never in this file
    python3 inventory.py

It writes devices.json and prints how many devices each site has. Two parts are yours to write:
get_page() and get_all(). Look for TODO.
"""
import json
import os
import random
import sys
import time
from collections import Counter

import requests

API = os.environ.get("LAB_API_URL", "http://api:8000")


class Client:
    """A logged-in session with the lab API."""

    def __init__(self):
        self.token = None
        self.requests = 0  # how many HTTP requests this client sent, retries included

    def login(self):
        """Get a fresh token, with the username and password from the environment."""
        try:
            user, password = os.environ["LAB_USERNAME"], os.environ["LAB_PASSWORD"]
        except KeyError:
            sys.exit("Set LAB_USERNAME and LAB_PASSWORD in the environment first.")
        r = requests.post(f"{API}/api/v1/auth/token", json={"username": user, "password": password}, timeout=5)
        r.raise_for_status()
        self.token = r.json()["token"]
        print(f"logged in as {user}")

    def get_page(self, url):
        """GET one URL and return the response once it's a success (2xx).

        TODO (lab): send the request with your token, then deal with what comes back:
          - 401: the token has expired (Step 6) — log in again, and send the same request again;
          - 429: wait the seconds in Retry-After, then send the same request again;
          - 503, or no response at all (requests.ConnectionError, requests.Timeout): wait, then
            try again — 1 s, then 2, 4, 8… plus a little randomness — and give up after 6 tries;
          - anything else that isn't a success: stop, loudly (r.raise_for_status()).
        Count every request you send in self.requests.
        """
        if self.token is None:
            self.login()
        # TODO: your code here
        raise NotImplementedError("write get_page()")

    def get_all(self, url):
        """Follow the pages from url to the end. Return (all the items, the X-Total-Count header).

        TODO (lab): call get_page() for the first URL, collect its "items", and keep going with
        the response's "next" URL until it's null. Read X-Total-Count from the first response.
        """
        items, total = [], None
        # TODO: your code here
        return items, total


def main():
    c = Client()
    devices, total = c.get_all(f"{API}/api/v1/devices?limit=10")
    if not devices:
        sys.exit("get_all() returned no devices: write get_page() and get_all() first (look for TODO).")
    with open("devices.json", "w") as f:
        json.dump(devices, f, indent=2)
    print(f"{len(devices)} devices read (the API says {total}), in {c.requests} requests; saved devices.json")
    for site, n in sorted(Counter(d["site"] for d in devices).items()):
        print(f"  {site}: {n}")
    if total is None or len(devices) != int(total):
        sys.exit("The count doesn't match X-Total-Count: some devices are missing.")


if __name__ == "__main__":
    main()
