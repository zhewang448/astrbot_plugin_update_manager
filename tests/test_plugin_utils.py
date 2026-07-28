import unittest

from plugin_utils import (
    build_market_index,
    clean_version,
    find_market_entry,
    is_valid_version,
    normalize_repo,
    normalize_weekdays,
    parse_check_times,
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


<<<<<<< Updated upstream
=======
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


>>>>>>> Stashed changes
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


if __name__ == "__main__":
    unittest.main()
