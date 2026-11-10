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
    def run_archive(self, fresh, pending, successful):
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
            stack.enter_context(patch.object(bot, "load_archive", return_value=([post(1)], "old-sha")))
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
        self.assertEqual([e["id"] for e in json.loads(self.files["pending_posts.json"])], ["8", "7", "6", "5", "2"])
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

    def test_browser_navigation_has_a_deadline(self):
        with patch.object(bot.webdriver, "Chrome") as chrome:
            self.assertIs(bot._open_list_browser(), chrome.return_value)
            chrome.return_value.set_page_load_timeout.assert_called_once_with(45)

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
