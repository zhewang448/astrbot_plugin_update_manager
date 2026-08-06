"""插件市场、自定义更新源和定时配置的纯函数工具。"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import yaml


PLUGIN_PREFIX = "astrbot_plugin_"
WEEKDAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: 可以安全套用 GitHub 加速前缀的主机。
#: 刻意不包含 api.github.com：多数加速服务不代理 API 域名，而且 API 请求
#: 会携带 Authorization 头，不应经过第三方中转。
PROXYABLE_GITHUB_HOSTS = frozenset(
    {
        "github.com",
        "raw.githubusercontent.com",
        "codeload.github.com",
        "objects.githubusercontent.com",
    }
)

CHANGELOG_FILENAMES = (
    "CHANGELOG.md",
    "changelog.md",
    "Changelog.md",
    "CHANGELOG.MD",
    "CHANGELOG",
    "changelog",
    "docs/CHANGELOG.md",
    "docs/changelog.md",
)

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*$")
_VERSION_TOKEN_RE = re.compile(r"v?(\d+(?:\.\d+)+(?:-[0-9A-Za-z.-]+)?)", re.IGNORECASE)


@dataclass
class MarketMatch:
    status: str
    entry: dict[str, Any] | None = None
    candidates: list[str] = field(default_factory=list)
    matched_by: str | None = None


@dataclass
class MarketIndex:
    by_repo: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_author_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_name: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass(frozen=True)
class CustomSourceBinding:
    plugin: str
    owner: str
    repo: str
    branch: str = ""

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def repo_id(self) -> str:
        return f"{self.owner}/{self.repo}"


def apply_github_proxy(url: str, proxy: str) -> str:
    """为可加速的 GitHub 地址套上 URL 前缀式加速服务。

    只处理 :data:`PROXYABLE_GITHUB_HOSTS` 中的主机；其他地址（含
    api.github.com）原样返回。已经带有加速前缀的地址不会被重复包装。
    """
    target = str(url or "").strip()
    prefix = str(proxy or "").strip().rstrip("/")
    if not target or not prefix:
        return target

    parsed = urlparse(target)
    if parsed.scheme not in ("http", "https"):
        return target
    if parsed.netloc.lower().removeprefix("www.") not in PROXYABLE_GITHUB_HOSTS:
        return target

    # 加速地址本身指向 GitHub 时不做处理，避免出现自我嵌套。
    proxy_parsed = urlparse(prefix if "://" in prefix else f"https://{prefix}")
    if proxy_parsed.netloc.lower().removeprefix("www.") in PROXYABLE_GITHUB_HOSTS:
        return target
    if not proxy_parsed.scheme:
        prefix = f"https://{prefix}"

    return f"{prefix}/{target}"


class BoundedCache:
    """带容量上限的 LRU 缓存，用于存放 ETag 和响应体。

    自定义源会把 commit SHA 拼进 URL，上游每发一版就多出一个永不复用的
    键，因此必须限制条目数量，否则长期运行会一直增长。
    """

    def __init__(self, max_entries: int = 128):
        self.max_entries = max(1, int(max_entries))
        self._store: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def get(self, key: str) -> dict[str, Any] | None:
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self.max_entries:
            self._store.popitem(last=False)

    def __len__(self) -> int:
        return len(self._store)


def normalize_name(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def normalize_author(value: object) -> str:
    return str(value or "").strip().lower()


def normalize_repo(value: object) -> str:
    """将 GitHub 仓库及常见代理、分支 URL 统一为 owner/repo。"""
    raw = unquote(str(value or "").strip()).replace("\\", "/")
    if not raw:
        return ""

    github_match = re.search(
        r"(?<![A-Za-z0-9.-])(?:www\.)?github\.com(?::|/)+"
        r"(?P<owner>[^/?#]+)/(?P<repo>[^/?#]+)",
        raw,
        flags=re.IGNORECASE,
    )
    if github_match:
        owner = github_match.group("owner").lower()
        repo = re.sub(r"\.git$", "", github_match.group("repo"), flags=re.I)
        return f"{owner}/{repo.lower()}"

    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    host = parsed.netloc.lower().removeprefix("www.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) >= 2:
        parts = parts[:2]
    normalized_path = "/".join(parts).lower()
    normalized_path = re.sub(r"\.git$", "", normalized_path, flags=re.I)
    return "/".join(part for part in (host, normalized_path) if part)


def parse_github_repo_url(value: object) -> tuple[str, str] | None:
    """解析标准 GitHub 仓库地址，不接受代理地址和仓库内页面地址。"""
    raw = str(value or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    host = parsed.netloc.lower().removeprefix("www.")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if host != "github.com" or len(parts) != 2:
        return None

    owner = parts[0].strip()
    repo = re.sub(r"\.git$", "", parts[1].strip(), flags=re.I)
    github_name = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not owner or not repo or not github_name.fullmatch(owner):
        return None
    if not github_name.fullmatch(repo):
        return None
    return owner, repo


def parse_custom_source_bindings(
    values: object,
) -> tuple[dict[str, CustomSourceBinding], set[str], list[dict[str, str]]]:
    """校验自定义源配置，并返回有效绑定、已占用插件名和错误。"""
    if not isinstance(values, list):
        return {}, set(), [{"plugin": "", "error": "配置不是列表"}]

    bindings: dict[str, CustomSourceBinding] = {}
    claimed_plugins: set[str] = set()
    errors: list[dict[str, str]] = []
    duplicate_plugins: set[str] = set()

    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            errors.append({"plugin": "", "error": f"第 {index} 项不是有效对象"})
            continue

        plugin_value = item.get("plugin")
        if isinstance(plugin_value, list):
            selected = [str(value or "").strip() for value in plugin_value]
            selected = [value for value in selected if value]
            claimed_plugins.update(selected)
            if len(selected) != 1:
                errors.append(
                    {
                        "plugin": ", ".join(selected),
                        "error": "每条绑定必须且只能选择一个本地插件",
                    }
                )
                continue
            plugin = selected[0]
        else:
            plugin = str(plugin_value or "").strip()

        repo_url = str(item.get("repo") or "").strip()
        branch = str(item.get("branch") or "").strip()
        if plugin:
            claimed_plugins.add(plugin)
        if not plugin:
            errors.append({"plugin": "", "error": f"第 {index} 项未选择本地插件"})
            continue
        if plugin in bindings or plugin in duplicate_plugins:
            bindings.pop(plugin, None)
            duplicate_plugins.add(plugin)
            errors.append({"plugin": plugin, "error": "同一个插件只能绑定一个自定义源"})
            continue

        parsed_repo = parse_github_repo_url(repo_url)
        if not parsed_repo:
            errors.append(
                {
                    "plugin": plugin,
                    "error": "仓库地址必须是 https://github.com/owner/repo",
                }
            )
            continue
        if branch and (any(char.isspace() for char in branch) or "\x00" in branch):
            errors.append({"plugin": plugin, "error": "分支名称不能包含空白字符"})
            continue

        owner, repo = parsed_repo
        bindings[plugin] = CustomSourceBinding(
            plugin=plugin,
            owner=owner,
            repo=repo,
            branch=branch,
        )

    return bindings, claimed_plugins, errors


def parse_plugin_metadata(value: object) -> dict[str, str]:
    """解析远端插件 metadata.yaml，并提取更新检查所需字段。"""
    try:
        data = yaml.safe_load(str(value or ""))
    except yaml.YAMLError as exc:
        raise ValueError(f"metadata YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("metadata 内容不是对象")

    name_value = data.get("name")
    version_value = data.get("version")
    name = name_value.strip() if isinstance(name_value, str) else ""
    version = version_value.strip() if isinstance(version_value, str) else ""
    if not name:
        raise ValueError("metadata 缺少有效的字符串字段 name")
    if not version:
        raise ValueError("metadata 缺少有效的字符串字段 version")

    optional_fields: dict[str, str] = {}
    for field_name in ("author", "repo"):
        field_value = data.get(field_name)
        if field_value is None:
            optional_fields[field_name] = ""
        elif isinstance(field_value, str):
            optional_fields[field_name] = field_value.strip()
        else:
            raise ValueError(f"metadata 字段 {field_name} 必须是字符串")

    return {
        "name": name,
        "version": version,
        **optional_fields,
    }


def clean_version(value: object) -> str:
    return re.sub(r"^[vV]", "", str(value or "").strip())


def is_valid_version(value: object) -> bool:
    version = clean_version(value)
    return bool(
        re.fullmatch(
            r"[0-9]+(?:\.[0-9]+)*(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+.+)?",
            version,
        )
    )


def _append_unique(
    index: dict[str, list[dict[str, Any]]],
    key: str,
    entry: dict[str, Any],
) -> None:
    if not key:
        return
    bucket = index.setdefault(key, [])
    entry_id = entry["_market_id"]
    if all(item["_market_id"] != entry_id for item in bucket):
        bucket.append(entry)


def _iter_market_entries(market_data: object):
    if isinstance(market_data, dict):
        for key, value in market_data.items():
            if key == "$meta" or not isinstance(value, dict):
                continue
            yield str(key), value
    elif isinstance(market_data, list):
        for index, value in enumerate(market_data):
            if isinstance(value, dict):
                yield str(value.get("plugin_id") or value.get("id") or index), value


def build_market_index(market_data: object) -> MarketIndex:
    index = MarketIndex()
    for fallback_id, raw_entry in _iter_market_entries(market_data):
        market_id = str(
            raw_entry.get("plugin_id")
            or raw_entry.get("id")
            or fallback_id
        ).strip()
        key_author, _, key_name = market_id.partition("/")
        name = str(raw_entry.get("name") or key_name or fallback_id).strip()
        author = str(raw_entry.get("author") or (key_author if key_name else "")).strip()
        entry = {
            **raw_entry,
            "name": name,
            "author": author,
            "_market_id": market_id,
        }

        _append_unique(index.by_repo, normalize_repo(entry.get("repo")), entry)
        normalized_name = normalize_name(name)
        normalized_author = normalize_author(author)
        if normalized_author and normalized_name:
            _append_unique(
                index.by_author_name,
                f"{normalized_author}/{normalized_name}",
                entry,
            )
        _append_unique(index.by_name, normalized_name, entry)

    return index


def _resolve_candidates(
    candidates: list[dict[str, Any]],
    matched_by: str,
) -> MarketMatch | None:
    unique = {entry["_market_id"]: entry for entry in candidates}
    if len(unique) == 1:
        return MarketMatch(
            status="matched",
            entry=next(iter(unique.values())),
            matched_by=matched_by,
        )
    if len(unique) > 1:
        return MarketMatch(
            status="ambiguous",
            candidates=sorted(unique),
            matched_by=matched_by,
        )
    return None


def find_market_entry(local_plugin: dict[str, Any], index: MarketIndex) -> MarketMatch:
    repo = normalize_repo(local_plugin.get("repo"))
    if repo:
        result = _resolve_candidates(index.by_repo.get(repo, []), "repo")
        if result:
            return result

    author = normalize_author(local_plugin.get("author"))
    name = normalize_name(local_plugin.get("name"))
    aliases = [name]
    if name.startswith(PLUGIN_PREFIX):
        aliases.append(name[len(PLUGIN_PREFIX) :])
    elif name:
        aliases.append(f"{PLUGIN_PREFIX}{name}")

    if author:
        author_candidates: list[dict[str, Any]] = []
        for alias in aliases:
            author_candidates.extend(
                index.by_author_name.get(f"{author}/{alias}", [])
            )
        result = _resolve_candidates(author_candidates, "author_name")
        if result:
            return result

    candidates: list[dict[str, Any]] = []
    for alias in aliases:
        candidates.extend(index.by_name.get(alias, []))
    result = _resolve_candidates(candidates, "name")
    if result:
        return result
    return MarketMatch(status="not_found")


def version_sort_key(value: object) -> tuple[int, ...]:
    """把版本号转成可比较的数字元组，用于截取区间。"""
    cleaned = clean_version(value)
    core = re.split(r"[-+]", cleaned, maxsplit=1)[0]
    parts: list[int] = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def extract_changelog_range(
    text: object,
    from_version: object,
    to_version: object,
) -> str:
    """截取 (from_version, to_version] 区间内的 CHANGELOG 小节。

    不依赖标题层级或文件结构：扫描所有包含版本号的标题，按版本号大小
    判断是否落在区间内。这样即使文件里小节顺序错乱、或正文标题混在
    版本标题之间，也能得到正确结果。
    """
    content = str(text or "")
    if not content.strip():
        return ""

    lines = content.splitlines()
    sections: list[tuple[tuple[int, ...], int, int]] = []
    current: tuple[tuple[int, ...], int] | None = None

    for line_no, line in enumerate(lines):
        heading = _HEADING_RE.match(line)
        if not heading:
            continue
        token = _VERSION_TOKEN_RE.search(heading.group(2))
        if not token:
            continue
        version_key = version_sort_key(token.group(1))
        if not version_key:
            continue
        if current is not None:
            sections.append((current[0], current[1], line_no))
        current = (version_key, line_no)

    if current is not None:
        sections.append((current[0], current[1], len(lines)))
    if not sections:
        return ""

    low = version_sort_key(from_version)
    high = version_sort_key(to_version)

    selected = [
        (key, start, end)
        for key, start, end in sections
        if (not high or key <= high) and (not low or key > low)
    ]
    # 版本号无法解析时至少给出最新一节，避免整段留空。
    if not selected and high:
        selected = [max(sections, key=lambda item: item[0])]

    selected.sort(key=lambda item: item[0], reverse=True)
    blocks: list[str] = []
    for _, start, end in selected:
        block = "\n".join(lines[start:end]).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks).strip()


def parse_rate_limit_headers(headers: object) -> str:
    """从 GitHub 响应头识别限流，返回可读提示，未限流则返回空串。"""
    getter: Callable[[str], Any] | None = getattr(headers, "get", None)
    if not callable(getter):
        return ""

    remaining = str(getter("X-RateLimit-Remaining") or "").strip()
    if remaining != "0":
        return ""

    limit = str(getter("X-RateLimit-Limit") or "").strip()
    reset_raw = str(getter("X-RateLimit-Reset") or "").strip()
    reset_text = ""
    if reset_raw.isdigit():
        from datetime import datetime, timezone

        reset_at = datetime.fromtimestamp(int(reset_raw), tz=timezone.utc).astimezone()
        reset_text = f"，将于 {reset_at.strftime('%H:%M')} 恢复"

    quota = f"（每小时 {limit} 次）" if limit else ""
    return (
        f"GitHub API 限额已用尽{quota}{reset_text}。"
        "建议在配置页填写 github_token 以提高限额。"
    )


def truncate_text(value: object, max_chars: int, suffix: str = "……（已截断）") -> str:
    """限制单段文本长度，避免超出平台消息上限。"""
    text = str(value or "")
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


def find_local_changelog(plugin_dir: object) -> Path | None:
    """在插件目录内按常见文件名查找 CHANGELOG。"""
    if not plugin_dir:
        return None
    try:
        base = Path(str(plugin_dir)).resolve()
    except (OSError, ValueError):
        return None
    if not base.is_dir():
        return None

    for filename in CHANGELOG_FILENAMES:
        candidate = base / filename
        try:
            resolved = candidate.resolve()
            # 防止 CHANGELOG 是指向目录外的符号链接。
            resolved.relative_to(base)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def normalize_github_url_to_archive(
    url: str, default_branch: str = "main"
) -> tuple[str, str, str] | None:
    """将 GitHub 仓库 URL（含分支/tag/commit）转换为下载地址和元信息。

    支持的输入格式：
        - https://github.com/owner/repo
        - https://github.com/owner/repo/tree/branch-name
        - https://github.com/owner/repo/archive/refs/tags/v1.0.0.zip（已是归档地址，原样返回）
        - github.com/owner/repo（自动补 https://）

    返回 (owner, repo, archive_url) 或 None（无法解析时）。
    archive_url 是可下载的 .zip 地址。

    当输入不带分支/tag 时，使用 default_branch（默认 "main"）。
    调用方可先用 GitHub API 查默认分支，传给这个函数。
    """
    raw = str(url or "").strip()
    if not raw:
        return None

    # 补全 scheme
    if "://" not in raw:
        raw = f"https://{raw}"

    parsed = urlparse(raw)
    host = parsed.netloc.lower().removeprefix("www.")
    if host != "github.com":
        return None

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None

    owner, repo = parts[0], parts[1]
    # 去掉 .git 后缀
    repo = re.sub(r"\.git$", "", repo, flags=re.IGNORECASE)

    # 已经是 archive 地址？直接返回
    if len(parts) >= 3 and parts[2] == "archive":
        return (owner, repo, raw)

    # 提取分支/tag/commit
    ref = default_branch
    if len(parts) >= 4:
        segment_type = parts[2]  # tree / blob / commit / releases / ...
        if segment_type in ("tree", "blob", "commit"):
            ref = "/".join(parts[3:])  # 支持分支名里有斜杠
        elif segment_type == "releases" and len(parts) >= 5 and parts[3] == "tag":
            ref = f"refs/tags/{parts[4]}"

    # 构造归档下载地址
    archive_url = f"https://github.com/{owner}/{repo}/archive/{quote(ref, safe='')}.zip"
    return (owner, repo, archive_url)


def parse_check_times(values: object) -> tuple[list[str], list[str]]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return [], [str(values)] if values is not None else []

    valid: set[str] = set()
    invalid: list[str] = []
    for value in values:
        text = str(value or "").strip()
        match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", text)
        if match:
            valid.add(f"{match.group(1)}:{match.group(2)}")
        elif text:
            invalid.append(text)
    return sorted(valid), invalid


def normalize_weekdays(values: object) -> tuple[list[str], list[str]]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return [], [str(values)] if values is not None else []

    selected = {str(value or "").strip().lower() for value in values}
    valid = [day for day in WEEKDAY_ORDER if day in selected]
    invalid = sorted(value for value in selected if value and value not in WEEKDAY_ORDER)
    return valid, invalid
