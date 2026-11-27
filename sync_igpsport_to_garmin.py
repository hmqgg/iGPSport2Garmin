#!/usr/bin/env python3
"""
Sync iGPSport cycling activities to Garmin Connect.

This script runs periodically in GitHub Actions to download FIT files from iGPSport
and upload them to Garmin Connect, with filtering to avoid duplicates.
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import random
import tempfile
import time
from getpass import getpass
from io import BytesIO
from pathlib import Path
from typing import ClassVar
from zipfile import BadZipFile, ZipFile

import requests
from dateutil.parser import parse
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("igpsport-to-garmin")

# Constants
LAST_SYNC_FILE = "last_sync_date.json"
OVERLAP_BUFFER_MINUTES = 5  # Consider activities overlapping if within 5 minutes
GARMIN_SESSION_DIR = "garmin_session"  # Dir to store Garmin session data


class IGPSportClient:
    """Client for the iGPSport API."""

    REGION_CONFIG: ClassVar = {
        "cn": {
            "base_url": "https://prod.zh.igpsport.com/service",
            "origin": "https://login.passport.igpsport.cn",
            "referer": "https://login.passport.igpsport.cn/",
        },
        "global": {
            "base_url": "https://prod.en.igpsport.com/service",
            "origin": "https://login.passport.igpsport.com",
            "referer": "https://login.passport.igpsport.com/",
        },
    }

    def __init__(self, username: str, password: str, region: str = "cn"):
        self.username = username
        self.password = password
        self.token = None
        self.region = (region or "cn").lower()

        if self.region not in self.REGION_CONFIG:
            logger.warning(
                f"Unknown igpsport region '{self.region}', defaulting to 'cn'"
            )
            self.region = "cn"

        region_cfg = self.REGION_CONFIG[self.region]
        self.base_url = region_cfg["base_url"]
        self.session = requests.Session()
        self.session.headers.update(
            {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": region_cfg["origin"],
                "referer": region_cfg["referer"],
            }
        )

    def login(self) -> bool:
        """Login to iGPSport."""
        url = f"{self.base_url}/auth/account/login"
        data = {
            "username": self.username,
            "password": self.password,
            "appId": "igpsport-web",
        }

        try:
            response = self.session.post(url, json=data)
            response.raise_for_status()
            result = response.json()

            if result["code"] == 0 and "data" in result:
                access_token = result["data"]["access_token"]
                self.token = access_token
                self.session.headers.update({"authorization": f"Bearer {access_token}"})
                logger.info("Successfully logged in to iGPSport")
                return True
            else:
                logger.error(f"Login failed: {result.get('message', 'Unknown error')}")
                return False
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            logger.error(f"Error during login: {e}")
            return False

    def get_activities(self, page_no: int = 1, page_size: int = 20) -> dict:
        """Get list of activities."""
        if not self.token:
            logger.error("Not logged in. Call login() first.")
            return {}

        url = f"{self.base_url}/web-gateway/web-analyze/activity/queryMyActivity"
        params = {"pageNo": page_no, "pageSize": page_size, "reqType": 0, "sort": 1}

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            result = response.json()

            if result["code"] == 0 and "data" in result:
                return result["data"]
            else:
                logger.error(
                    f"Failed to get activities: {result.get('message', 'Unknown error')}"
                )
                return {}
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            logger.error(f"Error getting activities: {e}")
            return {}

    def get_activity_detail(self, ride_id: int) -> dict:
        """Get details for a specific activity."""
        if not self.token:
            logger.error("Not logged in. Call login() first.")
            return {}

        url = f"{self.base_url}/web-gateway/web-analyze/activity/queryActivityDetail/{ride_id}"

        try:
            response = self.session.get(url)
            response.raise_for_status()
            result = response.json()

            if result["code"] == 0 and "data" in result:
                return result["data"]
            else:
                logger.error(
                    f"Failed to get activity detail: {result.get('message', 'Unknown error')}"
                )
                return {}
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            logger.error(f"Error getting activity detail: {e}")
            return {}

    def download_fit_file(self, fit_url: str) -> bytes | None:
        """Download a FIT file from the given URL."""
        try:
            response = requests.get(fit_url)
            response.raise_for_status()
            return response.content
        except requests.RequestException as e:
            logger.error("Error downloading FIT file (%s)", type(e).__name__)
            return None


def garmin_client() -> Garmin:
    domain = (os.getenv("GARMIN_DOMAIN") or "garmin.com").strip().lower()
    if domain not in {"garmin.com", "garmin.cn"}:
        raise ValueError("GARMIN_DOMAIN must be garmin.com or garmin.cn")
    api = Garmin(is_cn=domain == "garmin.cn")
    # The SDK normally tries other transports after a 429. Use just its
    # ordinary mobile login so the next attempt waits for another run.
    api.client.skip_strategies = {
        "mobile+cffi",
        "widget+cffi",
        "portal+cffi",
        "portal+requests",
    }
    return api


def password_login(api: Garmin, *, interactive: bool = False) -> None:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if interactive:
        email = email or input("Garmin email: ").strip()
        password = password or getpass("Garmin password: ")
    if not email or not password:
        raise ValueError("Set GARMIN_TOKENS or both GARMIN_EMAIL and GARMIN_PASSWORD")
    status, state = api.client.login(email, password, return_on_mfa=True)
    if status == "needs_mfa":
        if not interactive:
            raise GarminConnectAuthenticationError(
                "Garmin requires a verification code"
            )
        api.client.resume_login(state, getpass("Garmin verification code: "))


def save_garmin_session(api: Garmin) -> None:
    if not (
        api.client.di_token and api.client.di_refresh_token and api.client.di_client_id
    ):
        raise GarminConnectAuthenticationError(
            "Garmin did not provide a renewable session"
        )
    api.client.dump(GARMIN_SESSION_DIR)
    # load() also binds the live client to this path for subsequent token rotation.
    api.client.load(GARMIN_SESSION_DIR)
    logger.info(
        "Saved renewable Garmin session to %s/garmin_tokens.json", GARMIN_SESSION_DIR
    )


def list_garmin_activities(api: Garmin, candidates: list[dict]) -> list:
    # Allow adjacent local dates when the source and Garmin display different time zones.
    first_day = min(a["start_time"].date() for a in candidates) - datetime.timedelta(
        days=1
    )
    last_day = max(
        (
            a["start_time"]
            + datetime.timedelta(seconds=a["duration"], minutes=OVERLAP_BUFFER_MINUTES)
        ).date()
        for a in candidates
    )
    last_day += datetime.timedelta(days=1)
    return api.get_activities_by_date(first_day.isoformat(), last_day.isoformat())


def connect_garmin(candidates: list[dict]) -> tuple[Garmin, list]:
    """Use the overlap lookup to validate the session before any upload."""
    api = garmin_client()
    seed = os.getenv("GARMIN_TOKENS")
    if (Path(GARMIN_SESSION_DIR) / "garmin_tokens.json").is_file():
        try:
            api.client.load(GARMIN_SESSION_DIR)
        except GarminConnectConnectionError:
            logger.warning("Saved Garmin session could not be read")
        else:
            try:
                return api, list_garmin_activities(api, candidates)
            except GarminConnectAuthenticationError:
                if seed:
                    raise
                logger.info("Saved Garmin session was rejected. Signing in once.")

    if seed:
        api.client.loads(seed)
    else:
        password_login(api)
    save_garmin_session(api)
    return api, list_garmin_activities(api, candidates)


def contains_fit(original: bytes, fit_data: bytes) -> bool:
    """Garmin serves the original FIT either directly or inside a ZIP."""
    if original == fit_data:
        return True
    try:
        with ZipFile(BytesIO(original)) as archive:
            return any(
                f.filename.lower().endswith(".fit")
                and f.file_size == len(fit_data)
                and archive.read(f) == fit_data
                for f in archive.infolist()
            )
    except BadZipFile:
        return False


def find_original_fit(
    api: Garmin, rows: list[dict], ride: dict, fit_data: bytes
) -> bool:
    # Time and duration only narrow the download list. Only matching bytes
    # confirm that Garmin stored this particular ride.
    for row in rows:
        start = parse(row["startTimeLocal"], ignoretz=True)
        if abs((start.date() - ride["start_time"].date()).days) > 1:
            continue
        near_start = abs((start - ride["start_time"]).total_seconds()) <= 60
        near_duration = ride["duration"] > 0 and any(
            isinstance(row.get(key), (float, int))
            and abs(row[key] - ride["duration"]) <= 5
            for key in ("duration", "movingDuration", "elapsedDuration")
        )
        if not row.get("activityId") or not (near_start or near_duration):
            continue
        try:
            original = api.download_activity(
                row["activityId"], api.ActivityDownloadFormat.ORIGINAL
            )
        except GarminConnectNotFoundError:
            continue
        if contains_fit(original, fit_data):
            return True
    return False


def confirm_import(api: Garmin, result: object, ride: dict, fit_data: bytes) -> None:
    activity_id = ride["activity_id"]
    receipt = result.get("detailedImportResult") if isinstance(result, dict) else None
    if isinstance(receipt, dict):
        if receipt.get("failures"):
            raise RuntimeError(
                f"Garmin reported an import failure for activity {activity_id}"
            )
        if receipt.get("successes"):
            return

    # A completed POST can return an uploadId with two empty result lists.
    # Read the result back while it is being indexed. Never repeat the POST here.
    logger.info("Checking Garmin's original FIT for activity %s", activity_id)
    for delay in (0, 3, 8, 15):
        if delay:
            time.sleep(delay)
        rows = list_garmin_activities(api, [ride])
        if find_original_fit(api, rows, ride, fit_data):
            logger.info(
                "Verified the original FIT on Garmin for activity %s", activity_id
            )
            return
    raise RuntimeError(f"Garmin has not confirmed import of activity {activity_id}")


def upload_fit(api: Garmin, fit_data: bytes, ride: dict) -> bool:
    """Return False when Garmin already has the activity (HTTP 409)."""
    activity_id = ride["activity_id"]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "activity.fit"
        path.write_bytes(fit_data)
        for attempt in range(4):
            try:
                result = api.upload_activity(str(path))
            except GarminConnectConnectionError as exc:
                status = getattr(exc.response, "status_code", None)
                if status == 409:
                    logger.info("Garmin already has activity %s", activity_id)
                    return False
                if status == 429:
                    raise GarminConnectTooManyRequestsError() from exc
                transient = (status is not None and status >= 500) or isinstance(
                    exc.__cause__, (requests.ConnectionError, requests.Timeout)
                )
                if attempt == 3 or not transient:
                    raise
                delay = 5 * (2**attempt) + random.uniform(0, 2)
                logger.warning(
                    "Transient Garmin upload failure for activity %s. Retrying in %.1fs",
                    activity_id,
                    delay,
                )
                time.sleep(delay)
            else:
                confirm_import(api, result, ride, fit_data)
                return True


def load_sync_state() -> tuple[datetime.datetime, str | None]:
    """Old checkpoints contain only a timestamp; recheck that day once."""
    path = Path(LAST_SYNC_FILE)
    if not path.exists():
        return datetime.datetime.now() - datetime.timedelta(days=30), None
    data = json.loads(path.read_text())
    return datetime.datetime.fromisoformat(data["last_sync_date"]).replace(
        tzinfo=None
    ), data.get("day_fingerprint")


def save_sync_state(sync_date: datetime.datetime, day_fingerprint: str) -> None:
    path = Path(LAST_SYNC_FILE)
    state = {
        "last_sync_date": sync_date.isoformat(),
        "day_fingerprint": day_fingerprint,
    }
    with tempfile.TemporaryDirectory(dir=path.parent) as directory:
        draft = Path(directory) / path.name
        draft.write_text(json.dumps(state))
        draft.replace(path)


def activities_overlap(
    start_time1: datetime.datetime,
    duration1: int,
    start_time2: datetime.datetime,
    duration2: int,
) -> bool:
    """Check if two activities overlap in time."""
    end_time1 = start_time1 + datetime.timedelta(seconds=duration1)
    end_time2 = start_time2 + datetime.timedelta(seconds=duration2)

    buffer = datetime.timedelta(minutes=OVERLAP_BUFFER_MINUTES)

    # Check if either activity is contained within the other (with buffer)
    return (
        (start_time1 - buffer <= start_time2 <= end_time1 + buffer)
        or (start_time1 - buffer <= end_time2 <= end_time1 + buffer)
        or (start_time2 - buffer <= start_time1 <= end_time2 + buffer)
        or (start_time2 - buffer <= end_time1 <= end_time2 + buffer)
    )


def day_fingerprint(ids) -> str:
    """Recognize a previously checked day without publishing ride IDs."""
    payload = json.dumps(sorted(str(activity_id) for activity_id in ids))
    return hashlib.sha256(payload.encode()).hexdigest()


def collect_activities_to_sync(
    igpsport_client: IGPSportClient,
    last_sync_date: datetime.datetime,
    checked_day: str | None,
) -> list[dict]:
    """Find source candidates before contacting Garmin."""
    cutoff = last_sync_date.date()
    page_size = 20
    page = 1
    recent = {}
    while True:
        data = igpsport_client.get_activities(page_no=page, page_size=page_size)
        rows = data.get("rows")
        if not isinstance(rows, list):
            raise TypeError("Failed to list activities from iGPSport")
        previous_count = len(recent)
        reached_cutoff = False
        for row in rows:
            # The newest-first list often has only a day (e.g. 2024.11.20).
            day = parse(row["startTime"]).date()
            if day < cutoff:
                reached_cutoff = True
            else:
                recent[row["rideId"]] = (day, row)
        if reached_cutoff or len(rows) < page_size:
            break
        if len(recent) == previous_count:
            raise RuntimeError("iGPSport returned a repeated page of activities")
        page += 1

    # Keep late arrivals on the checkpoint day eligible, even when their
    # detailed start time precedes the last upload.
    same_day = day_fingerprint(i for i, (day, _) in recent.items() if day == cutoff)
    candidates = []
    for activity_id, (day, activity) in recent.items():
        if day == cutoff and same_day == checked_day:
            continue
        detail = igpsport_client.get_activity_detail(activity_id)
        if not detail:
            raise RuntimeError(f"Failed to read iGPSport activity {activity_id}")
        fit_url = detail.get("fitUrl") or activity.get("fitOssPath")
        if not fit_url:
            logger.warning("No FIT file URL for activity %s", activity_id)
            continue
        candidates.append(
            {
                "activity_id": activity_id,
                "fit_url": fit_url,
                "start_time": parse(detail["startTime"], ignoretz=True),
                "duration": detail.get("totalTime", 0),
            }
        )
    return candidates


def sync() -> None:
    username = os.getenv("IGPSPORT_USERNAME")
    password = os.getenv("IGPSPORT_PASSWORD")
    if not username or not password:
        raise ValueError("Set IGPSPORT_USERNAME and IGPSPORT_PASSWORD")
    source = IGPSportClient(username, password, os.getenv("IGPSPORT_REGION"))
    if not source.login():
        raise RuntimeError("Failed to authenticate with iGPSport")

    last_sync, checked_day = load_sync_state()
    candidates = collect_activities_to_sync(source, last_sync, checked_day)
    if not candidates:
        logger.info("No new iGPSport activities to sync")
        return

    logger.info("Found %d iGPSport activities to check", len(candidates))
    garmin, recent = connect_garmin(candidates)
    garmin_times = [
        (parse(row["startTimeLocal"], ignoretz=True), row.get("duration", 0))
        for row in recent
    ]

    uploaded = 0
    for activity in candidates:
        activity_id = activity["activity_id"]
        start = activity["start_time"]
        duration = activity["duration"]
        if any(activities_overlap(start, duration, t, d) for t, d in garmin_times):
            logger.info(
                "Skipping activity %s due to overlap with a Garmin activity",
                activity_id,
            )
            continue
        fit_data = source.download_fit_file(activity["fit_url"])
        if not fit_data:
            raise RuntimeError(
                f"Failed to download FIT file for activity {activity_id}"
            )
        if find_original_fit(garmin, recent, activity, fit_data):
            logger.info(
                "Garmin already has the original FIT for activity %s", activity_id
            )
            continue
        if upload_fit(garmin, fit_data, activity):
            logger.info("Uploaded activity %s", activity_id)
            uploaded += 1
            time.sleep(2)

    # Commit the whole batch only after every candidate was confirmed. A retry
    # after a partial upload will find the earlier uploads in Garmin.
    latest = max(last_sync, max(activity["start_time"] for activity in candidates))
    fingerprint = day_fingerprint(
        a["activity_id"] for a in candidates if a["start_time"].date() == latest.date()
    )
    save_sync_state(latest, fingerprint)
    logger.info(
        "Sync complete. Uploaded %d, already in Garmin %d",
        uploaded,
        len(candidates) - uploaded,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--garmin-login",
        action="store_true",
        help="sign in locally (including MFA) and save tokens for Actions",
    )
    args = parser.parse_args(argv)
    try:
        if args.garmin_login:
            api = garmin_client()
            password_login(api, interactive=True)
            save_garmin_session(api)
        else:
            sync()
    except GarminConnectTooManyRequestsError:
        logger.error(
            "Garmin rate limit (HTTP 429). Wait before retrying. "
            "For scheduled runs, configure GARMIN_TOKENS from --garmin-login."
        )
        return 1
    except GarminConnectAuthenticationError as exc:
        logger.error(
            "Garmin authentication failed: %s. Renew GARMIN_TOKENS with --garmin-login.",
            exc,
        )
        return 1
    except (
        GarminConnectConnectionError,
        requests.RequestException,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
    ) as exc:
        logger.error("Sync failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
