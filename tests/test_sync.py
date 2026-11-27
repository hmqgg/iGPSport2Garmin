import json
import os
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

import requests
from garminconnect.client import Client

import sync_igpsport_to_garmin as sync

TOKENS = json.dumps(
    {
        "di_token": "fixture-access",
        "di_refresh_token": "fixture-refresh",
        "di_client_id": "fixture-client",
    }
)
SUCCESS = {"detailedImportResult": {"successes": [{"internalId": 42}], "failures": []}}


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(body).encode()
    return result


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(chdir(self.enterContext(tempfile.TemporaryDirectory())))
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "IGPSPORT_USERNAME": "rider",
                    "IGPSPORT_PASSWORD": "source-password",
                    "GARMIN_EMAIL": "rider@example.test",
                    "GARMIN_PASSWORD": "garmin-password",
                },
                clear=True,
            )
        )
        self.source = self.enterContext(
            patch.object(sync, "IGPSportClient", autospec=True)
        ).return_value
        self.source.login.return_value = True
        self.rows = [{"rideId": i, "startTime": "2026.11.26"} for i in (1, 2, 3)]
        self.source.get_activities.return_value = {"rows": self.rows}
        self.source.get_activity_detail.side_effect = lambda i: {
            "startTime": f"2026-11-26T{i:02}:00:00",
            "totalTime": 600,
            "fitUrl": f"fixture-{i}",
        }
        self.source.download_fit_file.return_value = b"fixture FIT"
        self.http = self.enterContext(
            patch.object(requests.Session, "request", autospec=True)
        )
        self.http.side_effect = AssertionError("Unexpected HTTP request")
        self.enterContext(patch.object(sync.time, "sleep"))
        self.state = Path(sync.LAST_SYNC_FILE)
        self.original = '{"last_sync_date": "2026-01-13T19:37:24"}'
        self.state.write_text(self.original)
        self.tokens = Path(sync.GARMIN_SESSION_DIR, "garmin_tokens.json")

    def test_three_candidates_and_login_429_send_one_credential_post(self):
        self.http.side_effect = [response(200, {}), response(429, {})]
        with self.assertLogs(sync.logger, "ERROR") as log:
            self.assertEqual(sync.main([]), 1)
        self.assertIn("429", log.output[-1])
        self.assertEqual([c.args[1] for c in self.http.call_args_list], ["GET", "POST"])
        self.assertIn("/mobile/api/login", self.http.call_args.args[2])
        self.source.download_fit_file.assert_not_called()
        self.assertEqual(self.state.read_text(), self.original)
        self.assertFalse(self.tokens.exists())

    def test_no_candidates_never_contacts_garmin(self):
        self.source.get_activities.return_value = {
            "rows": [{"rideId": 7, "startTime": "2025.12.15"}]
        }
        with patch.object(sync, "garmin_client") as make_client:
            self.assertEqual(sync.main([]), 0)
            make_client.assert_not_called()
        self.assertEqual(self.state.read_text(), self.original)

    def test_seeded_session_overlap_conflict_upload_then_idle_and_late_arrival(self):
        os.environ["GARMIN_TOKENS"] = TOKENS
        os.environ["GARMIN_DOMAIN"] = "garmin.cn"
        del os.environ["GARMIN_PASSWORD"]
        del os.environ["GARMIN_EMAIL"]
        overlap = {"startTimeLocal": "2026-11-26T01:04:00", "duration": 600}
        self.http.side_effect = [
            response(200, [overlap]),
            response(200, []),
            response(409, {}),
            response(201, SUCCESS),
        ]
        self.assertEqual(sync.main([]), 0)
        self.assertEqual(
            [c.args[1] for c in self.http.call_args_list],
            ["GET", "GET", "POST", "POST"],
        )
        self.assertIn(".garmin.cn/", self.http.call_args.args[2])
        self.assertEqual(json.loads(self.tokens.read_text()), json.loads(TOKENS))
        self.assertEqual(
            json.loads(self.state.read_text())["last_sync_date"], "2026-11-26T03:00:00"
        )

        del os.environ["GARMIN_TOKENS"]
        self.http.reset_mock()
        self.assertEqual(sync.main([]), 0)
        self.http.assert_not_called()

        # A ride from earlier on the same day can still arrive after the watermark.
        self.rows.append({"rideId": 0, "startTime": "2026.11.26"})
        previous = [
            {"startTimeLocal": f"2026-11-26T0{i}:00:00", "duration": 600}
            for i in (1, 2, 3)
        ]
        self.http.side_effect = [
            response(200, previous),
            response(200, []),
            response(201, SUCCESS),
        ]
        self.assertEqual(sync.main([]), 0)
        self.source.download_fit_file.assert_called_with("fixture-0")
        self.assertEqual(
            json.loads(self.state.read_text())["last_sync_date"], "2026-11-26T03:00:00"
        )

    def test_garmin_lookup_error_stops_before_downloading(self):
        os.environ["GARMIN_TOKENS"] = TOKENS
        self.http.side_effect = [response(429, {})]
        self.assertEqual(sync.main([]), 1)
        self.assertEqual(self.http.call_count, 1)
        self.source.download_fit_file.assert_not_called()
        self.assertEqual(self.state.read_text(), self.original)

    def test_partial_upload_leaves_batch_retryable(self):
        os.environ["GARMIN_TOKENS"] = TOKENS
        self.http.side_effect = [
            response(200, []),
            response(201, SUCCESS),
            response(429, {}),
        ]
        self.assertEqual(sync.main([]), 1)
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(self.state.read_text(), self.original)

        already_uploaded = [{"startTimeLocal": "2026-11-26T01:00:00", "duration": 600}]
        self.source.download_fit_file.reset_mock()
        self.http.side_effect = [
            response(200, already_uploaded),
            response(200, []),
            response(201, SUCCESS),
            response(201, SUCCESS),
        ]
        self.assertEqual(sync.main([]), 0)
        self.assertEqual(
            [c.args[0] for c in self.source.download_fit_file.call_args_list],
            ["fixture-2", "fixture-3"],
        )

    def test_unconfirmed_import_does_not_advance_checkpoint(self):
        os.environ["GARMIN_TOKENS"] = TOKENS
        self.http.side_effect = [
            response(200, []),
            response(200, {"detailedImportResult": {"failures": [{}]}}),
        ]
        self.assertEqual(sync.main([]), 1)
        self.assertEqual(self.state.read_text(), self.original)

    def test_password_login_replaces_legacy_garth_files_and_uploads(self):
        self.tokens.parent.mkdir()
        (self.tokens.parent / "oauth1_token.json").write_text("{}")
        self.rows[:] = self.rows[:1]
        self.http.side_effect = [
            response(200, {}),
            response(
                200,
                {
                    "responseStatus": {"type": "SUCCESSFUL"},
                    "serviceTicketId": "fixture-ticket",
                },
            ),
            response(200, {"displayName": "rider"}),
            response(200, []),
            response(201, SUCCESS),
        ]
        with patch.object(
            Client,
            "_di_token_post",
            return_value=response(
                200,
                {
                    "access_token": "fixture-access",
                    "refresh_token": "fixture-refresh",
                },
            ),
        ):
            self.assertEqual(sync.main([]), 0)
        sso_posts = [
            c
            for c in self.http.call_args_list
            if c.args[1] == "POST" and "/mobile/api/login" in c.args[2]
        ]
        self.assertEqual(len(sso_posts), 1)
        self.assertEqual(
            json.loads(self.tokens.read_text())["di_refresh_token"], "fixture-refresh"
        )

    def test_refresh_rotation_is_written_even_if_the_batch_then_fails(self):
        self.tokens.parent.mkdir()
        self.tokens.write_text(TOKENS)
        self.http.side_effect = [response(401, {}), response(429, {})]
        with patch.object(
            Client,
            "_di_token_post",
            return_value=response(
                200,
                {
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                },
            ),
        ):
            self.assertEqual(sync.main([]), 1)
        self.assertEqual(
            json.loads(self.tokens.read_text())["di_refresh_token"], "new-refresh"
        )
        self.assertEqual(self.state.read_text(), self.original)

    def test_mfa_in_actions_exits_without_prompting(self):
        self.http.side_effect = [
            response(200, {}),
            response(200, {"responseStatus": {"type": "MFA_REQUIRED"}}),
        ]
        with patch.object(sync, "getpass") as prompt:
            self.assertEqual(sync.main([]), 1)
            prompt.assert_not_called()
        self.assertFalse(self.tokens.exists())
        self.assertEqual(self.state.read_text(), self.original)

    def test_local_login_can_finish_mfa_without_running_sync(self):
        def resume(client, state, code):
            self.assertEqual((state, code), ({"mfa": "state"}, "123456"))
            client.loads(TOKENS)

        with (
            patch.object(Client, "login", return_value=("needs_mfa", {"mfa": "state"})),
            patch.object(Client, "resume_login", autospec=True, side_effect=resume),
            patch.object(sync, "getpass", return_value="123456"),
        ):
            self.assertEqual(sync.main(["--garmin-login"]), 0)
        self.assertEqual(json.loads(self.tokens.read_text()), json.loads(TOKENS))
        self.source.login.assert_not_called()

    def test_november_cutoff_includes_the_first_day(self):
        self.state.write_text('{"last_sync_date":"2026-11-01T00:00:00"}')
        self.source.get_activities.return_value = {
            "rows": [
                {"rideId": 1, "startTime": "2026.10.31"},
                {"rideId": 2, "startTime": "2026.11.01"},
            ]
        }
        self.source.get_activity_detail.side_effect = None
        self.source.get_activity_detail.return_value = {
            "startTime": "2026-11-01T00:00:00",
            "fitUrl": "november-fit",
        }
        candidates = sync.collect_activities_to_sync(
            self.source, *sync.load_sync_state()
        )
        self.assertEqual([a["activity_id"] for a in candidates], [2])
        self.source.get_activity_detail.assert_called_once_with(2)

    def test_first_run_after_long_gap_includes_the_next_source_page(self):
        rows = [{"rideId": i, "startTime": "2026.11.26"} for i in range(21)]
        self.source.get_activities.side_effect = [
            {"rows": rows[:20]},
            {"rows": rows[20:]},
        ]
        last_sync, checked_day = sync.load_sync_state()
        candidates = sync.collect_activities_to_sync(
            self.source, last_sync, checked_day
        )
        self.assertEqual([a["activity_id"] for a in candidates], list(range(21)))


if __name__ == "__main__":
    unittest.main()
