"""配置加载与校验测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from boss_tool.config import (
    BrowserConfig,
    BudgetConfig,
    ConfigLoader,
    PageIntervalConfig,
    RunControlConfig,
    load_config,
)


class TestDefaultConfig:
    def test_load_default_config(self, real_config_dir: Path):
        cfg = load_config(real_config_dir)
        assert cfg["app"].app_name == "boss-tool"
        assert cfg["app"].candidate_age == 60
        assert "保安" in cfg["keywords"].keywords
        assert len(cfg["keywords"].keywords) == 6
        assert cfg["location"].city == "杭州市"
        assert cfg["location"].district == "拱墅区"
        assert cfg["runtime"].browser.headless is False
        assert cfg["runtime"].browser.single_context is True
        assert cfg["runtime"].browser.single_account is True
        assert cfg["runtime"].run_control.require_user_confirm is True
        assert cfg["runtime"].run_control.allow_unattended is False
        assert cfg["runtime"].run_control.allow_background is False
        assert sum(cfg["scoring"].weights.values()) == 100

    def test_loader_returns_all_keys(self, real_config_dir: Path):
        cfg = ConfigLoader(real_config_dir).load_all()
        for k in (
            "app",
            "keywords",
            "location",
            "runtime",
            "scoring",
            "age_rules_raw",
            "intensity_rules_raw",
        ):
            assert k in cfg


class TestMissingConfig:
    def test_missing_required_file(self, tmp_workspace: Path):
        cfg_dir = tmp_workspace / "config"
        cfg_dir.mkdir()
        with pytest.raises(FileNotFoundError) as exc:
            load_config(cfg_dir)
        assert "缺少必需文件" in str(exc.value)

    def test_missing_app_yaml(self, copied_config_dir: Path):
        (copied_config_dir / "app.yaml").unlink()
        with pytest.raises(FileNotFoundError):
            load_config(copied_config_dir)

    def test_empty_yaml_file(self, copied_config_dir: Path):
        (copied_config_dir / "keywords.yaml").write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="为空"):
            load_config(copied_config_dir)


class TestTypeValidation:
    def test_invalid_budget_type(self, copied_config_dir: Path):
        runtime_path = copied_config_dir / "runtime.yaml"
        data = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
        data["budget"]["max_search_pages_per_keyword"] = "not-a-number"
        runtime_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(copied_config_dir)

    def test_unknown_field_rejected(self, copied_config_dir: Path):
        runtime_path = copied_config_dir / "runtime.yaml"
        data = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
        data["budget"]["unknown_field"] = 999
        runtime_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(copied_config_dir)


class TestBudgetValidation:
    def _patch_runtime(self, cfg_dir: Path, **overrides):
        runtime_path = cfg_dir / "runtime.yaml"
        data = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
        for section, kvs in overrides.items():
            data.setdefault(section, {}).update(kvs)
        runtime_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def test_budget_zero_rejected(self, copied_config_dir: Path):
        self._patch_runtime(copied_config_dir, budget={"max_runtime_minutes": 0})
        with pytest.raises(ValidationError):
            load_config(copied_config_dir)

    def test_budget_negative_rejected(self, copied_config_dir: Path):
        self._patch_runtime(copied_config_dir, budget={"max_errors_per_run": -1})
        with pytest.raises(ValidationError):
            load_config(copied_config_dir)

    def test_budget_config_directly(self):
        with pytest.raises(ValidationError):
            BudgetConfig(
                max_search_pages_per_keyword=0,
                max_job_details_per_run=1,
                max_total_pages_per_run=1,
                max_runtime_minutes=1,
                max_errors_per_run=1,
                max_consecutive_parse_failures=1,
            )


class TestPageIntervalValidation:
    def test_min_gt_max_rejected(self):
        with pytest.raises(ValidationError):
            PageIntervalConfig(min_seconds=20, max_seconds=10)

    def test_min_equal_max_ok(self):
        cfg = PageIntervalConfig(min_seconds=10, max_seconds=10)
        assert cfg.min_seconds == cfg.max_seconds


class TestBrowserConfigValidation:
    def test_headless_true_rejected(self):
        with pytest.raises(ValidationError, match="headless"):
            BrowserConfig(
                user_data_dir="./user_data",
                headless=True,
                single_context=True,
                single_account=True,
            )

    def test_single_context_false_rejected(self):
        with pytest.raises(ValidationError, match="single_context"):
            BrowserConfig(
                user_data_dir="./user_data",
                headless=False,
                single_context=False,
                single_account=True,
            )

    def test_single_account_false_rejected(self):
        with pytest.raises(ValidationError, match="single_account"):
            BrowserConfig(
                user_data_dir="./user_data",
                headless=False,
                single_context=True,
                single_account=False,
            )


class TestRunControlValidation:
    def test_require_user_confirm_false_rejected(self):
        with pytest.raises(ValidationError, match="require_user_confirm"):
            RunControlConfig(
                require_user_confirm=False,
                allow_unattended=False,
                allow_background=False,
            )

    def test_allow_unattended_true_rejected(self):
        with pytest.raises(ValidationError, match="allow_unattended"):
            RunControlConfig(
                require_user_confirm=True,
                allow_unattended=True,
                allow_background=False,
            )

    def test_allow_background_true_rejected(self):
        with pytest.raises(ValidationError, match="allow_background"):
            RunControlConfig(
                require_user_confirm=True,
                allow_unattended=False,
                allow_background=True,
            )


class TestScoringValidation:
    def test_scoring_weights_must_sum_100(self, copied_config_dir: Path):
        scoring_path = copied_config_dir / "scoring.yaml"
        data = yaml.safe_load(scoring_path.read_text(encoding="utf-8"))
        data["weights"]["age_match"] = 20  # 总和变成 90
        scoring_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValidationError, match="权重总和"):
            load_config(copied_config_dir)


class TestLocationValidation:
    def test_invalid_longitude_rejected(self, copied_config_dir: Path):
        loc_path = copied_config_dir / "locations.yaml"
        data = yaml.safe_load(loc_path.read_text(encoding="utf-8"))
        data["location"]["center_longitude"] = 999.0
        loc_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(copied_config_dir)


class TestKeywordValidation:
    def test_empty_keywords_rejected(self, copied_config_dir: Path):
        kw_path = copied_config_dir / "keywords.yaml"
        data = yaml.safe_load(kw_path.read_text(encoding="utf-8"))
        data["keywords"] = ["", "  "]
        kw_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        with pytest.raises(ValidationError):
            load_config(copied_config_dir)
