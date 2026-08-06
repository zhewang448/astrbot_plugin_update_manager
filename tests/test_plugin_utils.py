import unittest

from plugin_utils import (
    apply_github_proxy,
    build_market_index,
    clean_version,
    extract_changelog_range,
    find_market_entry,
    is_valid_version,
    normalize_repo,
    normalize_weekdays,
    parse_check_times,
    parse_custom_source_bindings,
    parse_github_repo_url,
    parse_plugin_metadata,
    parse_rate_limit_headers,
    truncate_text,
    version_sort_key,
)


class MarketMatchingTests(unittest.TestCase):
    def test_new_market_format_and_meta_are_supported(self):
        market = {
            "$meta": {"generated_at": "now"},
            "Alice/astrbot_plugin_demo": {
                "version": "2.0.0",
                "repo": "https://github.com/Alice/astrbot_plugin_demo",
            },
        }
        index = build_market_index(market)
        result = find_market_entry(
            {
                "name": "astrbot_plugin_demo",
                "author": "Alice",
                "repo": "",
            },
            index,
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.entry["_market_id"], "Alice/astrbot_plugin_demo")
        self.assertEqual(result.matched_by, "author_name")

    def test_repo_match_has_priority(self):
        market = {
            "Alice/demo": {
                "name": "same_name",
                "repo": "https://github.com/Alice/demo",
            },
            "Bob/demo": {
                "name": "same_name",
                "repo": "https://github.com/Bob/demo",
            },
        }
        result = find_market_entry(
            {
                "name": "same_name",
                "author": "unknown",
                "repo": "https://github.com/Bob/demo/tree/test",
            },
            build_market_index(market),
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.entry["_market_id"], "Bob/demo")
        self.assertEqual(result.matched_by, "repo")

    def test_author_and_name_distinguish_duplicate_names(self):
        market = {
            "Alice/demo": {"name": "demo"},
            "Bob/demo": {"name": "demo"},
        }
        result = find_market_entry(
            {"name": "demo", "author": "Bob", "repo": ""},
            build_market_index(market),
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.entry["_market_id"], "Bob/demo")

    def test_author_match_supports_prefix_alias(self):
        market = {
            "Alice/demo": {"name": "demo"},
            "Bob/demo": {"name": "demo"},
        }
        result = find_market_entry(
            {"name": "astrbot_plugin_demo", "author": "Bob", "repo": ""},
            build_market_index(market),
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.entry["_market_id"], "Bob/demo")
        self.assertEqual(result.matched_by, "author_name")

    def test_unique_name_fallback_supports_prefix_alias(self):
        market = {"Alice/demo": {"name": "demo"}}
        result = find_market_entry(
            {"name": "astrbot_plugin_demo", "author": "", "repo": ""},
            build_market_index(market),
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.matched_by, "name")

    def test_duplicate_name_is_ambiguous(self):
        market = {
            "Alice/demo": {"name": "demo"},
            "Bob/demo": {"name": "demo"},
        }
        result = find_market_entry(
            {"name": "demo", "author": "unknown", "repo": ""},
            build_market_index(market),
        )
        self.assertEqual(result.status, "ambiguous")
        self.assertEqual(result.candidates, ["Alice/demo", "Bob/demo"])

    def test_missing_plugin_is_not_found(self):
        result = find_market_entry(
            {"name": "missing", "author": "", "repo": ""},
            build_market_index({}),
        )
        self.assertEqual(result.status, "not_found")


class NormalizationTests(unittest.TestCase):
    def test_github_branch_url_is_normalized(self):
        self.assertEqual(
            normalize_repo("https://github.com/Owner/Repo.git/tree/test/"),
            "owner/repo",
        )

    def test_github_proxy_url_is_normalized(self):
        self.assertEqual(
            normalize_repo(
                "https://gh-proxy.com/https://github.com/Owner/Repo/archive/main.zip"
            ),
            "owner/repo",
        )

    def test_github_like_domain_is_not_treated_as_github(self):
        self.assertEqual(
            normalize_repo("https://notgithub.com/Owner/Repo"),
            "notgithub.com/owner/repo",
        )

    def test_www_github_url_is_normalized(self):
        self.assertEqual(
            normalize_repo("https://www.github.com/Owner/Repo"),
            "owner/repo",
        )

    def test_version_prefix_is_removed(self):
        self.assertEqual(clean_version("V2.4.0"), "2.4.0")

    def test_invalid_version_is_rejected(self):
        self.assertTrue(is_valid_version("v2.4.0-beta.1"))
        self.assertFalse(is_valid_version("development"))


class CustomSourceTests(unittest.TestCase):
    def test_standard_github_repo_url_is_parsed(self):
        self.assertEqual(
            parse_github_repo_url("https://github.com/Owner/Repo.git"),
            ("Owner", "Repo"),
        )

    def test_repository_subpage_and_proxy_are_rejected(self):
        self.assertIsNone(parse_github_repo_url("https://github.com/Owner/Repo/tree/main"))
        self.assertIsNone(
            parse_github_repo_url("https://gh-proxy.com/https://github.com/Owner/Repo")
        )

    def test_single_selected_plugin_creates_binding(self):
        bindings, claimed, errors = parse_custom_source_bindings([
            {
                "plugin": ["astrbot_plugin_demo"],
                "repo": "https://github.com/Owner/Repo",
                "branch": "develop",
            }
        ])
        self.assertFalse(errors)
        self.assertEqual(claimed, {"astrbot_plugin_demo"})
        self.assertEqual(bindings["astrbot_plugin_demo"].repo_id, "Owner/Repo")
        self.assertEqual(bindings["astrbot_plugin_demo"].branch, "develop")

    def test_multiple_selected_plugins_are_rejected(self):
        bindings, claimed, errors = parse_custom_source_bindings([
            {
                "plugin": ["plugin_a", "plugin_b"],
                "repo": "https://github.com/Owner/Repo",
            }
        ])
        self.assertFalse(bindings)
        self.assertEqual(claimed, {"plugin_a", "plugin_b"})
        self.assertIn("只能选择一个", errors[0]["error"])

    def test_duplicate_bindings_are_all_disabled(self):
        bindings, claimed, errors = parse_custom_source_bindings([
            {"plugin": "plugin_a", "repo": "https://github.com/A/One"},
            {"plugin": "plugin_a", "repo": "https://github.com/B/Two"},
        ])
        self.assertFalse(bindings)
        self.assertEqual(claimed, {"plugin_a"})
        self.assertIn("只能绑定一个", errors[0]["error"])

    def test_metadata_yaml_is_parsed(self):
        metadata = parse_plugin_metadata(
            "name: astrbot_plugin_demo\nversion: v1.2.3\n"
            "repo: https://github.com/Owner/Repo\n"
        )
        self.assertEqual(metadata["name"], "astrbot_plugin_demo")
        self.assertEqual(metadata["version"], "v1.2.3")

    def test_metadata_requires_name_and_version(self):
        with self.assertRaisesRegex(ValueError, "缺少.*version"):
            parse_plugin_metadata("name: astrbot_plugin_demo\n")
        with self.assertRaisesRegex(ValueError, "不是对象"):
            parse_plugin_metadata("- item\n")

    def test_metadata_rejects_non_string_fields(self):
        with self.assertRaisesRegex(ValueError, "字符串字段 version"):
            parse_plugin_metadata("name: astrbot_plugin_demo\nversion: 1.10\n")
        with self.assertRaisesRegex(ValueError, "字段 repo 必须是字符串"):
            parse_plugin_metadata(
                "name: astrbot_plugin_demo\nversion: v1.2.3\nrepo: 123\n"
            )


class ScheduleParsingTests(unittest.TestCase):
    def test_times_are_validated_deduplicated_and_sorted(self):
        valid, invalid = parse_check_times(
            ["16:00", "04:00", "16:00", "24:00", "9:00", "bad"]
        )
        self.assertEqual(valid, ["04:00", "16:00"])
        self.assertEqual(invalid, ["24:00", "9:00", "bad"])

    def test_weekdays_are_normalized_and_ordered(self):
        valid, invalid = normalize_weekdays(["SUN", "mon", "fri", "holiday"])
        self.assertEqual(valid, ["mon", "fri", "sun"])
        self.assertEqual(invalid, ["holiday"])


class ChangelogTests(unittest.TestCase):
    SAMPLE = (
        "## v1.3.0\n- 新增 A\n- 修复 B\n\n"
        "## v1.2.0\n- 改进 C\n\n"
        "## v1.1.0\n- 初始版本\n"
    )

    def test_exact_range_returns_single_section(self):
        text = extract_changelog_range(self.SAMPLE, "1.2.0", "1.3.0")
        self.assertIn("新增 A", text)
        self.assertNotIn("改进 C", text)

    def test_multi_version_jump_includes_intermediate_sections(self):
        text = extract_changelog_range(self.SAMPLE, "1.1.0", "1.3.0")
        self.assertIn("新增 A", text)
        self.assertIn("改进 C", text)
        self.assertNotIn("初始版本", text)

    def test_from_zero_returns_all_sections(self):
        text = extract_changelog_range(self.SAMPLE, "", "1.3.0")
        self.assertIn("新增 A", text)
        self.assertIn("改进 C", text)
        self.assertIn("初始版本", text)

    def test_empty_content_returns_empty_string(self):
        self.assertEqual(extract_changelog_range("", "1.0.0", "2.0.0"), "")

    def test_out_of_order_heading_before_main_title_is_handled(self):
        # CHANGELOG 里版本小节出现在 # 标题之前的情况（本仓库曾经的格式问题）。
        messy = (
            "## v2.4.2\n- 新功能\n\n"
            "# 更新日志\n\n"
            "## v2.4.1\n- 旧功能\n"
        )
        text = extract_changelog_range(messy, "2.4.1", "2.4.2")
        self.assertIn("新功能", text)
        self.assertNotIn("旧功能", text)

    def test_version_sort_key_parses_correctly(self):
        self.assertEqual(version_sort_key("v2.4.1"), (2, 4, 1))
        self.assertEqual(version_sort_key("1.10.0"), (1, 10, 0))
        self.assertGreater(version_sort_key("1.10.0"), version_sort_key("1.9.0"))
        self.assertEqual(version_sort_key("not-a-version"), ())


class ProxyAndCacheTests(unittest.TestCase):
    def test_raw_github_url_gets_prefix(self):
        result = apply_github_proxy(
            "https://raw.githubusercontent.com/owner/repo/main/file.txt",
            "https://gh-proxy.com",
        )
        self.assertEqual(
            result,
            "https://gh-proxy.com/https://raw.githubusercontent.com/owner/repo/main/file.txt",
        )

    def test_github_archive_url_gets_prefix(self):
        result = apply_github_proxy(
            "https://github.com/owner/repo/archive/abc123.zip",
            "https://gh-proxy.com",
        )
        self.assertIn("gh-proxy.com", result)
        self.assertIn("abc123.zip", result)

    def test_api_github_com_is_not_proxied(self):
        url = "https://api.github.com/repos/owner/repo"
        self.assertEqual(apply_github_proxy(url, "https://gh-proxy.com"), url)

    def test_empty_proxy_returns_url_unchanged(self):
        url = "https://raw.githubusercontent.com/owner/repo/main/file"
        self.assertEqual(apply_github_proxy(url, ""), url)

    def test_non_github_url_is_not_proxied(self):
        url = "https://example.com/file.zip"
        self.assertEqual(apply_github_proxy(url, "https://gh-proxy.com"), url)

    def test_proxy_pointing_to_github_itself_is_skipped(self):
        # 避免将 github.com 本身当加速前缀造成自我嵌套。
        url = "https://raw.githubusercontent.com/owner/repo/main/file"
        self.assertEqual(apply_github_proxy(url, "https://github.com"), url)


class RateLimitTests(unittest.TestCase):
    def _headers(self, remaining, limit="60", reset=""):
        return {
            "X-RateLimit-Remaining": remaining,
            "X-RateLimit-Limit": limit,
            "X-RateLimit-Reset": reset,
        }

    def test_non_zero_remaining_returns_empty(self):
        self.assertEqual(parse_rate_limit_headers(self._headers("10")), "")

    def test_zero_remaining_returns_hint(self):
        hint = parse_rate_limit_headers(self._headers("0", "60"))
        self.assertIn("限额已用尽", hint)
        self.assertIn("github_token", hint)

    def test_none_headers_returns_empty(self):
        self.assertEqual(parse_rate_limit_headers(None), "")

    def test_truncate_text_cuts_at_limit(self):
        text = "a" * 100
        result = truncate_text(text, 20)
        self.assertLessEqual(len(result), 20 + len("……（已截断）"))
        self.assertIn("已截断", result)

    def test_truncate_text_leaves_short_text_unchanged(self):
        text = "hello"
        self.assertEqual(truncate_text(text, 100), text)


if __name__ == "__main__":
    unittest.main()
