"""验证仓库同时兼容单 Skill 根目录模式与 Claude Code / Codex / Antigravity 插件仓库规范。"""
from __future__ import annotations
import json
import subprocess
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ANTIGRAVITY_ALLOWED_PLUGIN_KEYS = {
    "name",
    "displayName",
    "version",
    "description",
    "logo",
    "suggestedPrompts",
    "author",
    "homepage",
    "repository",
    "disabled",
}


class PluginAndSkillCompatTests(unittest.TestCase):
    def test_project_and_plugin_versions_match(self):
        project = json.loads((ROOT / "project.json").read_text(encoding="utf-8"))
        agy_plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
        claude_plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude_market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        codex_plugin = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))

        expected_name = project["name"]
        expected_version = project["version"]

        self.assertEqual(agy_plugin["name"], expected_name)
        self.assertEqual(agy_plugin["version"], expected_version)
        self.assertTrue(set(agy_plugin.keys()).issubset(ANTIGRAVITY_ALLOWED_PLUGIN_KEYS))
        self.assertLessEqual(len(agy_plugin.get("suggestedPrompts", [])), 3)
        self.assertTrue((ROOT / agy_plugin["logo"]).is_file())

        self.assertEqual(claude_plugin["name"], expected_name)
        self.assertEqual(claude_plugin["version"], expected_version)
        self.assertEqual(claude_market["name"], expected_name)
        self.assertEqual(claude_market["plugins"][0]["name"], expected_name)
        self.assertEqual(claude_market["plugins"][0]["version"], expected_version)
        self.assertEqual(claude_market["plugins"][0]["source"], "./")

        self.assertEqual(codex_plugin["name"], expected_name)
        self.assertEqual(codex_plugin["version"], expected_version)
        self.assertTrue(codex_plugin["interface"]["displayName"])

    def test_both_skill_entrypoints_and_openai_yaml_exist(self):
        root_skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        nested_skill = (ROOT / "skills" / "h3-studio-colab" / "SKILL.md").read_text(encoding="utf-8")
        for content in (root_skill, nested_skill):
            self.assertTrue(content.startswith("---\nname: h3-studio-colab\ndescription: "))

        for yaml_path in (
            ROOT / "agents" / "openai.yaml",
            ROOT / "skills" / "h3-studio-colab" / "agents" / "openai.yaml",
        ):
            text = yaml_path.read_text(encoding="utf-8")
            self.assertIn('display_name: "H3 Studio Colab"', text)
            icon_dir = yaml_path.parent.parent
            self.assertTrue((icon_dir / "assets" / "logo.svg").is_file())
            ET.parse(icon_dir / "assets" / "logo.svg")

    def test_plugin_nested_script_forwards_to_root_cli(self):
        shim = ROOT / "skills" / "h3-studio-colab" / "scripts" / "h3studio.py"
        proc = subprocess.run(
            [sys.executable, str(shim), "plan", str(ROOT / "examples" / "basic-v3.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        data = json.loads(proc.stdout)
        self.assertIn("jobs", data)


if __name__ == "__main__":
    unittest.main()
