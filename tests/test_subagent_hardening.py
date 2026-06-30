"""Phase-1 hardening tests for the subagent dispatch boundary."""

import os
import tempfile
import unittest
from unittest import mock

import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO, "src"))

import subagent  # noqa: E402


class SubagentBoundaryTests(unittest.TestCase):
    def test_cloud_budget_fallback_denies_by_default_when_budget_unavailable(self):
        cfg = {"node_role": "cloud_specialist", "base_url": "https://api.example.test/v1"}
        with mock.patch.dict(os.environ, {"THREADKEEPER_SRC_DIR": "/does/not/exist"}, clear=False), \
             mock.patch.object(subagent, "__file__", "/tmp/no-threadkeeper-budget/subagent.py"):
            os.environ.pop("OMEGACLAW_SUBAGENT_BUDGET_FALLBACK", None)
            allowed, reason = subagent._escalation_gate(cfg)
        self.assertFalse(allowed)
        self.assertIn("fail-closed deny", reason)

    def test_cloud_budget_fallback_can_be_explicitly_fail_opened(self):
        cfg = {"node_role": "cloud_specialist", "base_url": "https://api.example.test/v1"}
        with mock.patch.dict(os.environ, {
            "THREADKEEPER_SRC_DIR": "/does/not/exist",
            "OMEGACLAW_SUBAGENT_BUDGET_FALLBACK": "allow",
        }, clear=False), mock.patch.object(subagent, "__file__", "/tmp/no-threadkeeper-budget/subagent.py"):
            allowed, reason = subagent._escalation_gate(cfg)
        self.assertTrue(allowed)
        self.assertIn("explicit fallback", reason)

    def test_file_tools_are_confined_to_workspace(self):
        with tempfile.TemporaryDirectory() as workspace, mock.patch.dict(
            os.environ, {"OMEGACLAW_SUBAGENT_WORKSPACE": workspace}, clear=False
        ):
            self.assertEqual(subagent._tool_write_file("nested/ok.txt", "safe"), "WRITE-FILE-SUCCESS")
            self.assertEqual(subagent._tool_read_file("nested/ok.txt"), "safe")
            denied = subagent._tool_read_file("../outside.txt")
            self.assertIn("escapes subagent workspace", denied)

    def test_shell_disabled_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            out = subagent._tool_shell("echo hello")
        self.assertIn("disabled by default", out)

    def test_shell_requires_allowlisted_executable_and_does_not_use_shell_metacharacters(self):
        with mock.patch.dict(os.environ, {
            "OMEGACLAW_SUBAGENT_ENABLE_SHELL": "1",
            "OMEGACLAW_SUBAGENT_SHELL_ALLOWLIST": "printf",
        }, clear=True):
            denied = subagent._tool_shell("echo hello")
            out = subagent._tool_shell("printf 'a;b'")
        self.assertIn("not allowlisted", denied)
        self.assertEqual(out, "a;b")


if __name__ == "__main__":
    unittest.main()
