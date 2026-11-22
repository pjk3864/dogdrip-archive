import tempfile
import unittest
from pathlib import Path

from sync_repository_lists import sync


class SyncListsTests(unittest.TestCase):
    def test_updates_all_pages_without_commit_dependent_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site, target = root / 'site', root / 'target'
            site.mkdir()
            target.mkdir()
            commit = 'a' * 40
            for name in ('index.html', 'page-2.html', 'page-3.html'):
                (site / name).write_text('archive.json?v=' + commit, encoding='utf-8')
            self.assertEqual(len(sync(site, target, commit)), 3)
            self.assertEqual((target / 'index.html').read_text(encoding='utf-8'), 'archive.json')
            self.assertEqual((target / 'page-3.html').read_text(encoding='utf-8'), 'archive.json')

    def test_rejects_incomplete_pages_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / 'site'
            site.mkdir()
            (site / 'index.html').write_text('index', encoding='utf-8')
            (site / 'page-3.html').write_text('page', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                sync(site, Path(tmp), 'a' * 40)
            self.assertFalse((Path(tmp) / 'index.html').exists())


if __name__ == '__main__':
    unittest.main()
