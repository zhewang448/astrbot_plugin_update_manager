"""插件市场、自定义更新源和定时配置的纯函数工具。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import yaml


PLUGIN_PREFIX = "astrbot_plugin_"
WEEKDAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


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
        r"github\.com(?::|/)+(?P<owner>[^/?#]+)/(?P<repo>[^/?#]+)",
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

    name = str(data.get("name") or "").strip()
    version = str(data.get("version") or "").strip()
    if not name:
        raise ValueError("metadata 缺少 name")
    if not version:
        raise ValueError("metadata 缺少 version")
    return {
        "name": name,
        "version": version,
        "author": str(data.get("author") or "").strip(),
        "repo": str(data.get("repo") or "").strip(),
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
