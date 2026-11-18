"""按需查询 GitHub 发行版元数据。此模块不请求下载附件。"""
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


REPOSITORY = 'ElmaTony/Ad-Image-Filter-GUI'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
LATEST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
EXE_NAME = 'AdImageFilter-Windows-x64.exe'
TOKEN_ENV = 'ADFILTER_GITHUB_TOKEN'
_VERSION = re.compile(r'v?(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})', re.ASCII)
_HISTORICAL = {
    'v2026.10.30': '1.0.0',
    'v2026.10.30.1': '1.1.0',
    'v2026.11.09': '1.2.0',
    'v2026.11.14': '1.2.1',
    'v2026.11.15': '1.3.0',
    'v2026.11.18': '1.3.1',
}


class UpdateCheckError(Exception):
    """可安全显示在界面中的检查失败原因。"""


@dataclass(frozen=True)
class Update:
    version: str
    release_url: str
    download_url: str | None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _request_latest(token=None):
    headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
               'User-Agent': 'AdImageFilter-UpdateCheck'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    request = Request(LATEST_API, headers=headers)
    with build_opener(_NoRedirect()).open(request, timeout=8) as response:
        limit = 512 * 1024
        body = response.read(limit + 1)
        if len(body) > limit:
            raise ValueError('Release metadata exceeds the size limit')
        return json.loads(body)


def _git_token():
    """读取本机 Git 凭据助手的可用凭据，禁止交互式登录。"""
    executable = 'git.exe' if os.name == 'nt' else 'git'
    # 仅查找绝对 PATH 项，避免从图片目录或当前目录执行同名程序。
    paths = (Path(entry) for entry in os.get_exec_path())
    git = next((path / executable for path in paths if path.is_absolute() and (path / executable).is_file()), None)
    if git is None:
        return None
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'}
    try:
        result = subprocess.run(
            [str(git), '-c', 'credential.interactive=never', 'credential', 'fill'],
            input=f'protocol=https\nhost=github.com\npath={REPOSITORY}.git\n\n'.encode('ascii'),
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=git.parent, env=env, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            key, _, value = line.partition(b'=')
            if key == b'password' and value.isascii():
                return value.decode('ascii') or None
    return None


def _latest_metadata():
    token = os.environ.get(TOKEN_ENV, '').strip()
    if token:
        return _request_latest(token)
    try:
        return _request_latest()
    except HTTPError as error:
        if error.code not in (401, 403, 404, 429):
            raise
        error.close()
        token = _git_token()
        if not token:
            raise UpdateCheckError('无法读取此仓库的发行版。请使用有访问权限的 GitHub 账号登录后查看。') from None
    return _request_latest(token)


def _version_parts(value):
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError('Invalid release version')
    return tuple(int(part) for part in match.groups())


def check_latest(current_version: str) -> Update | None:
    """返回新版和可信下载地址，已是最新版或更高时返回 None。"""
    try:
        release = _latest_metadata()
        tag = release['tag_name']
        version = _version_parts(_HISTORICAL.get(tag, tag))
        if release.get('draft') or release.get('prerelease'):
            raise ValueError('Expected a published stable release')
        if version <= _version_parts(current_version):
            return None

        # 链接仅使用符合预期的项目、标签和安装包名称。
        url = f'{RELEASES_URL}/download/{tag}/{EXE_NAME}'
        assets = release.get('assets', [])
        found = any(asset.get('name') == EXE_NAME and asset.get('state') == 'uploaded' and
                    asset.get('browser_download_url') == url for asset in assets if isinstance(asset, dict))
        return Update('v' + '.'.join(map(str, version)), f'{RELEASES_URL}/tag/{tag}', url if found else None)
    except HTTPError as error:
        error.close()
        if error.code in (401, 403, 404, 429):
            raise UpdateCheckError('GitHub 暂未授权访问或限制了请求。可打开发行页查看，或稍后重试。') from None
        raise UpdateCheckError('GitHub 暂时无法返回发行版，请稍后重试。') from None
    except (OSError, http.client.HTTPException):
        raise UpdateCheckError('无法连接 GitHub，请检查网络后重试。') from None
    except (AttributeError, KeyError, TypeError, ValueError):
        raise UpdateCheckError('无法读取 GitHub 发行版的版本信息。') from None
