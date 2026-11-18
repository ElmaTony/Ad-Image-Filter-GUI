# -*- coding: utf-8 -*-
"""更新检查和「关于」界面测试，使用模拟发行数据及独立临时目录。"""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ad_filter as af
import update_check as updates


def release(tag, *, asset=True, url=None):
    canonical = f'{updates.RELEASES_URL}/download/{tag}/{updates.EXE_NAME}'
    files = [{'name': updates.EXE_NAME, 'state': 'uploaded', 'browser_download_url': url or canonical}]
    return {'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': files if asset else []}


class UpdateCheckTests(unittest.TestCase):
    def test_compares_numbers_and_ignores_lower_and_historical_versions(self):
        for remote, local, expected in [('v1.10.0', '1.9.9', 'v1.10.0'), ('v1.4.0', '1.4.0', None),
                                        ('v1.4.0', '1.4.1', None), ('v2026.11.18', '1.3.2', None),
                                        ('v2026.10.30.1', '1.0.0', 'v1.1.0')]:
            with self.subTest(remote=remote, local=local), patch.object(updates, '_latest_metadata', return_value=release(remote)):
                result = updates.check_latest(local)
                self.assertEqual(result.version if result else None, expected)

    def test_missing_or_untrusted_asset_uses_only_project_release_page(self):
        for payload in (release('v1.5.0', asset=False), release('v1.5.0', url='https://example.invalid/setup.exe')):
            with patch.object(updates, '_latest_metadata', return_value=payload):
                result = updates.check_latest('1.4.0')
            self.assertIsNone(result.download_url)
            self.assertEqual(result.release_url, f'{updates.RELEASES_URL}/tag/v1.5.0')

    def test_network_and_invalid_release_errors_are_readable(self):
        for problem in (URLError('internal detail'), TimeoutError('internal detail')):
            with patch.object(updates, '_latest_metadata', side_effect=problem):
                with self.assertRaisesRegex(updates.UpdateCheckError, '无法连接 GitHub'):
                    updates.check_latest('1.4.0')
        for payload in ({}, release('v1.5.0-rc1'), {**release('v1.5.0'), 'draft': True}):
            with patch.object(updates, '_latest_metadata', return_value=payload):
                with self.assertRaisesRegex(updates.UpdateCheckError, '版本信息'):
                    updates.check_latest('1.4.0')

    def test_private_repository_uses_existing_login_after_anonymous_denial(self):
        payload = release('v1.5.0')
        denied = HTTPError(updates.LATEST_API, 404, 'Not Found', None, None)
        with patch.dict(os.environ, {updates.TOKEN_ENV: ''}), \
                patch.object(updates, '_git_token', return_value='fixture-token') as git, \
                patch.object(updates, '_request_latest', side_effect=[denied, payload]) as request:
            result = updates.check_latest('1.4.0')
        self.assertEqual(result.download_url, payload['assets'][0]['browser_download_url'])
        git.assert_called_once_with()
        self.assertEqual([call.args for call in request.call_args_list], [(), ('fixture-token',)])

    def test_private_repository_without_access_reports_failure(self):
        denied = HTTPError(updates.LATEST_API, 404, 'Not Found', None, None)
        with patch.dict(os.environ, {updates.TOKEN_ENV: ''}), \
                patch.object(updates, '_git_token', return_value=None), \
                patch.object(updates, '_request_latest', side_effect=denied):
            with self.assertRaisesRegex(updates.UpdateCheckError, '有访问权限'):
                updates.check_latest('1.4.0')


class AboutUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        from workbench_ui import configure_fonts
        configure_fonts()
        directory = TemporaryDirectory(prefix='adfilter_update_test_')
        self.addCleanup(directory.cleanup)
        for name in ('CONFIG_PATH', 'CACHE_PATH', 'HISTORY_PATH', 'LOG_PATH'):
            override = patch.object(af, name, Path(directory.name) / name)
            override.start()
            self.addCleanup(override.stop)

    def open_dialog(self):
        from PySide6.QtWidgets import QWidget
        from dialog_ui import AboutDialog
        self.parent = QWidget()
        self.dialog = AboutDialog(self.parent, '1.4.0')
        self.addCleanup(self.dialog.close)
        self.dialog.show()
        self.qt.processEvents()

    def wait_result(self):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            self.qt.processEvents()
            if self.dialog.check_button.isEnabled():
                return
            time.sleep(.01)
        self.fail('关于窗口未显示检查结果')

    def test_manual_nonblocking_check_displays_download_link(self):
        payload = release('v1.5.0')
        gate = threading.Event()

        def fetch():
            self.assertTrue(gate.wait(2))
            return payload

        with patch.object(updates, '_latest_metadata', side_effect=fetch) as metadata:
            self.open_dialog()
            metadata.assert_not_called()
            self.dialog.check_button.click()
            self.assertFalse(self.dialog.check_button.isEnabled())
            self.assertIn('正在检查', self.dialog.update_status.text())
            self.dialog.check_button.click()
            gate.set()
            self.wait_result()
            metadata.assert_called_once_with()
        self.assertEqual(self.dialog.update_status.text(), '发现新版 v1.5.0。')
        self.assertTrue(self.dialog.update_link.isVisible())
        self.assertIn(payload['assets'][0]['browser_download_url'], self.dialog.update_link.text())

    def test_retry_clears_failure_link_when_already_current(self):
        with patch.object(updates, '_latest_metadata', side_effect=[URLError('offline'), release('v1.4.0')]):
            self.open_dialog()
            self.dialog.check_button.click()
            self.wait_result()
            self.assertIn('无法连接 GitHub', self.dialog.update_status.text())
            self.assertIn(updates.RELEASES_URL, self.dialog.update_link.text())
            self.dialog.check_button.click()
            self.wait_result()
        self.assertEqual(self.dialog.update_status.text(), '当前版本无需更新。')
        self.assertFalse(self.dialog.update_link.isVisible())


if __name__ == '__main__':
    unittest.main(verbosity=2)
