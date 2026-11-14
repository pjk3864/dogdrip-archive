import contextlib
import copy
import datetime
import io
import json
import os
import unittest
from unittest.mock import patch

import automation_health
import bot


def post(number):
    return {"id": str(number), "title": str(number), "source_url": str(number),
            "thumbnail_url": "", "votes": "0", "comments": "0", "published": ""}


class DailyArchiveTests(unittest.TestCase):
    def run_archive(self, fresh, pending, successful, existing=None):
        self.files = {}
        self.requested = []
        self.checkpoints = []

        def snapshot(url):
            self.requested.append(url)
            if url not in successful:
                raise RuntimeError("source unavailable")
            return {"title": url, "content": "<p>saved</p>", "comments": []}

        def put(path, content, message, sha=None):
            self.files[path] = content
            if path == "archive.json":
                self.checkpoints.append((len(self.requested), json.loads(content)))

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {"CI": "true", "ARCHIVE_PENDING_ONLY": "0"}))
            stack.enter_context(patch.object(bot, "load_archive", return_value=(existing or [post(1)], "old-sha")))
            stack.enter_context(patch.object(bot, "get_manual_posts", return_value=[]))
            stack.enter_context(patch.object(bot, "github_get_file", return_value=(json.dumps(pending).encode(), "queue-sha")))
            stack.enter_context(patch.object(bot, "get_popular_posts", return_value=copy.deepcopy(fresh)))
            stack.enter_context(patch.object(bot, "get_post_snapshot", side_effect=snapshot))
            stack.enter_context(patch.object(bot, "localize_comment_media", return_value=[]))
            stack.enter_context(patch.object(bot, "localize_article_content", return_value="<p>saved</p>"))
            stack.enter_context(patch.object(bot, "archive_asset", return_value=""))
            stack.enter_context(patch.object(bot, "github_put_file", side_effect=put))
            build = stack.enter_context(patch.object(bot, "build_list_pages"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            bot.archive_posts()
            build.assert_not_called()

    def test_partial_success_is_checkpointed_before_later_failures(self):
        self.run_archive([post(i) for i in (5, 6, 7, 8, 9)], [post(2)], {"9"})
        self.assertEqual(self.requested, ["9", "8", "7", "6"])
        self.assertEqual(self.checkpoints[0][0], 1)
        self.assertEqual([e["id"] for e in json.loads(self.files["archive.json"])], ["9", "1"])
        self.assertCountEqual([e["id"] for e in json.loads(self.files["pending_posts.json"])], ["8", "7", "6", "5", "2"])
        self.assertIn("posts/9.html", self.files)
        self.assertNotIn("posts/8.html", self.files)

    def test_total_outage_preserves_queue_and_signals_failure(self):
        with self.assertRaisesRegex(RuntimeError, "성공한 새 글 0개"):
            self.run_archive([post(i) for i in (5, 6, 7, 8)], [post(2)], set())
        self.assertEqual(self.requested, ["8", "7", "6"])
        self.assertNotIn("archive.json", self.files)
        self.assertEqual(len(json.loads(self.files["pending_posts.json"])), 5)

    def test_success_resets_consecutive_failure_count(self):
        self.run_archive([post(i) for i in (5, 6, 7, 8)], [], {"7", "5"})
        self.assertEqual(self.requested, ["8", "7", "6", "5"])
        self.assertEqual([e["id"] for e in json.loads(self.files["archive.json"])], ["7", "5", "1"])

    def test_current_window_retries_older_queue_without_dropping_it(self):
        self.run_archive([post(50)], [post(2)], {"50"}, existing=[post(100)])
        self.assertEqual(bot.MAX_POPULAR_PAGES, 10)
        self.assertEqual(self.requested, ["50", "2"])
        self.assertEqual([e["id"] for e in json.loads(self.files["archive.json"])], ["100", "50"])
        self.assertEqual([e["id"] for e in json.loads(self.files["pending_posts.json"])], ["2"])

    def test_pending_retry_is_bounded_after_current_posts(self):
        self.run_archive([post(100)], [post(i) for i in range(2, 24)], {"100"})
        self.assertEqual(self.requested[0], "100")
        self.assertEqual(len(self.requested), 4)  # three source failures trigger backoff
        self.assertEqual(len(json.loads(self.files["pending_posts.json"])), 22)

    def test_github_rule_timeout_is_transient(self):
        from unittest.mock import Mock
        from requests import Response
        transient = Mock(spec=Response)
        transient.status_code = 403
        transient.text = '{"message":"The rule did not run because of a timeout."}'
        transient.headers = {}
        final = Mock(spec=Response)
        final.status_code = 200
        final.text = '{}'
        final.headers = {}
        with patch.object(bot.requests, "request", side_effect=[transient, final]) as request, \
             patch.object(bot, "_github_headers", return_value={}), \
             patch.object(bot.time, "sleep") as sleep:
            result = bot._github_request("GET", "https://api.github.com/test")
        self.assertIs(result, final)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once()

    def test_missing_article_body_raises_instead_of_archiving_placeholder(self):
        with patch.object(bot, "_get_dogdrip_document_html", return_value="<html><body></body></html>"):
            with self.assertRaisesRegex(RuntimeError, "Missing article body"):
                bot.get_post_snapshot("https://www.dogdrip.net/123")

    def test_browser_navigation_has_a_deadline(self):
        with patch.object(bot.webdriver, "Chrome") as chrome:
            self.assertIs(bot._open_list_browser(), chrome.return_value)
            chrome.return_value.set_page_load_timeout.assert_called_once_with(45)

    def test_public_article_uses_http_without_starting_chrome(self):
        page = '<div class="document_123_0 rhymix_content xe_content">body</div>'
        with patch.object(bot.requests, "get") as get, \
             patch.object(bot, "_get_document_browser") as browser, \
             patch.object(bot, "_last_dogdrip_document_request", 0):
            get.return_value.text = page
            self.assertEqual(bot._get_dogdrip_document_html("https://www.dogdrip.net/123"), page)
            browser.assert_not_called()

    def test_http_challenge_page_falls_back_to_chrome(self):
        with patch.object(bot.requests, "get") as get, \
             patch.object(bot, "_get_document_browser") as browser, \
             patch.object(bot, "WebDriverWait") as wait, \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_last_dogdrip_document_request", 0):
            get.return_value.text = '<html><title>Just a moment...</title></html>'
            browser.return_value.page_source = "browser article"
            self.assertEqual(bot._get_dogdrip_document_html("https://www.dogdrip.net/123"), "browser article")
            browser.return_value.get.assert_called_once()
            wait.return_value.until.assert_called_once()

    def test_collection_marker_requires_completion_after_ten_bangkok(self):
        zone = datetime.timezone(datetime.timedelta(hours=7), "Asia/Bangkok")
        current = datetime.datetime(2026, 11, 12, 12, tzinfo=zone)
        status = {"local_date": "2026-11-12", "timezone": "Asia/Bangkok",
                  "completed_at": "2026-11-12T09:59:00+07:00"}
        self.assertFalse(automation_health.healthy_today(status, current))
        status["completed_at"] = "2026-11-12T10:00:00+07:00"
        self.assertTrue(automation_health.healthy_today(status, current))

    def test_recovery_before_ten_does_not_start_collection(self):
        zone = datetime.timezone(datetime.timedelta(hours=7), "Asia/Bangkok")
        current = datetime.datetime(2026, 11, 12, 9, 59, tzinfo=zone)
        with patch.object(automation_health, "now_local", return_value=current), \
             patch.object(automation_health, "archive_sha", return_value="saved"), \
             patch.object(automation_health, "public_manifest", return_value={"archive_sha": "saved"}), \
             patch.object(automation_health, "recover_workflow") as recover, \
             contextlib.redirect_stdout(io.StringIO()):
            automation_health.check()
            recover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
