"""Tests for prompt file structural contracts.

Validates that all agent prompt files in agent/prompts/ contain the
required structural sections that the system depends on at runtime.

Covers:
- All 7 prompt roles (analyst, assigner, employee, manager, planner,
  reviewer, triager) have corresponding .md files
- Each prompt contains an <identity> section
- PROMPT_ROLES in the prompts router stays in sync with actual files
- Agent Teams agent-definition files exist and the orchestrator's
  TEAMMATE_ROLES constant is in sync
- Prompt files are non-trivially sized (not stubs)
"""

from pathlib import Path

import pytest


# Resolve paths relative to the test file
_TESTS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _TESTS_DIR.parent  # dashboard/backend
_PROJECT_ROOT = _BACKEND_DIR.parent.parent  # project root
_PROMPTS_DIR = _PROJECT_ROOT / "agent" / "prompts"


# The 7 actual prompt roles (excluding REPORT-SCHEMAS.md which is a reference doc)
ALL_PROMPT_ROLES = [
    "analyst",
    "assigner",
    "employee",
    "manager",
    "planner",
    "reviewer",
    "triager",
]


class TestPromptFilesExist:
    """Verify every expected prompt file is present on disk."""

    @pytest.mark.parametrize("role", ALL_PROMPT_ROLES)
    def test_prompt_file_exists(self, role: str):
        """Each role must have a corresponding .md file."""
        path = _PROMPTS_DIR / f"{role}.md"
        assert path.is_file(), (
            f"Missing prompt file: {path}. "
            f"The agent system expects all 7 prompt roles to have files."
        )

    def test_prompts_directory_exists(self):
        """The agent/prompts/ directory must exist."""
        assert _PROMPTS_DIR.is_dir(), f"Prompts directory missing: {_PROMPTS_DIR}"

    def test_no_unexpected_role_files_missing(self):
        """Ensure we test exactly the roles that exist on disk."""
        actual_md_files = {
            p.stem for p in _PROMPTS_DIR.glob("*.md")
            if p.stem != "REPORT-SCHEMAS"  # reference doc, not a prompt
        }
        expected = set(ALL_PROMPT_ROLES)
        # All expected roles should have files
        missing = expected - actual_md_files
        assert not missing, f"Expected prompt files missing: {missing}"


class TestPromptStructure:
    """Verify each prompt file has the required structural sections."""

    @pytest.mark.parametrize("role", ALL_PROMPT_ROLES)
    def test_has_identity_section(self, role: str):
        """Each prompt must contain an <identity> XML tag section."""
        content = (_PROMPTS_DIR / f"{role}.md").read_text()
        assert "<identity>" in content, (
            f"Prompt '{role}.md' is missing <identity> section. "
            f"All prompts must define their identity."
        )
        assert "</identity>" in content, (
            f"Prompt '{role}.md' has unclosed <identity> tag."
        )

    @pytest.mark.parametrize("role", ALL_PROMPT_ROLES)
    def test_has_title_heading(self, role: str):
        """Each prompt should start with a markdown heading."""
        content = (_PROMPTS_DIR / f"{role}.md").read_text()
        assert content.strip().startswith("# "), (
            f"Prompt '{role}.md' should start with a # heading."
        )

    @pytest.mark.parametrize("role", ALL_PROMPT_ROLES)
    def test_minimum_content_length(self, role: str):
        """Prompt files must have non-trivial content (not stubs)."""
        content = (_PROMPTS_DIR / f"{role}.md").read_text()
        # Minimum 200 chars — a real prompt has instructions, not just a title
        assert len(content) >= 200, (
            f"Prompt '{role}.md' is suspiciously short ({len(content)} chars). "
            f"Expected a real system prompt with instructions."
        )


class TestPromptRouterSync:
    """Verify PROMPT_ROLES in the prompts router covers all prompt files.

    Issue finding #5: The prompt directory now has 7 roles but the router
    PROMPT_ROLES dict only defines 5. The roles 'reviewer' and 'triager'
    are missing from the router, which means they can't be customized
    via the dashboard UI.
    """

    def test_router_roles_are_subset_of_actual_files(self):
        """Every role in the router must have a corresponding prompt file."""
        from app.routers.prompts import PROMPT_ROLES

        for role, info in PROMPT_ROLES.items():
            file_path = _PROMPTS_DIR / info["file"]
            assert file_path.is_file(), (
                f"PROMPT_ROLES['{role}'] references '{info['file']}' "
                f"but file does not exist at {file_path}"
            )

    def test_router_has_correct_file_references(self):
        """Each router role should reference the correct filename."""
        from app.routers.prompts import PROMPT_ROLES

        for role, info in PROMPT_ROLES.items():
            expected_file = f"{role}.md"
            assert info["file"] == expected_file, (
                f"PROMPT_ROLES['{role}']['file'] is '{info['file']}' "
                f"but expected '{expected_file}'"
            )

    def test_router_roles_count(self):
        """Document the current router role count.

        Currently 5 roles in the router (manager, employee, analyst,
        planner, assigner). Reviewer and triager are missing.
        This test documents the gap — when they're added, update the count.
        """
        from app.routers.prompts import PROMPT_ROLES

        # Document the known gap: 7 files on disk, only 5 in router
        # Excludes: REPORT-SCHEMAS (shared schema doc), conflict_resolver
        # (system prompt for the agent.conflict_resolver harness, not an
        # orchestrator role agent — see
        # docs/superpowers/specs/2026-05-10-conflict-resolution-design.md).
        _NON_ROLE_PROMPTS = {"REPORT-SCHEMAS", "conflict_resolver"}
        actual_files_count = len([
            p for p in _PROMPTS_DIR.glob("*.md")
            if p.stem not in _NON_ROLE_PROMPTS
        ])
        router_count = len(PROMPT_ROLES)

        # This assertion documents the gap. Update both sides when fixed.
        # 8 agent role prompts (analyst, assigner, employee, manager, planner,
        # reviewer, security-reviewer, triager) + 2 vision-chat prompts
        # (vision_create, vision_refine — used by the dashboard chat backend,
        # not by orchestrator role agents) = 10 total.
        assert actual_files_count == 10, (
            f"Expected 10 prompt files on disk, found {actual_files_count}"
        )
        assert router_count == 6, (
            f"PROMPT_ROLES currently has {router_count} entries. "
            f"Expected 6 (triager not yet added to router; vision chat "
            f"prompts are not exposed via the prompts router)."
        )


_AGENTS_DIR = _PROJECT_ROOT / "agent" / "agents"


class TestAgentTeamsDefinitions:
    """Verify the Agent Teams agent-definition contract.

    Replaces the legacy `MODE_REGISTRY` test class. Under Agent Teams
    (Claude Agent SDK), there is no per-mode prompt registry — instead
    a single ``issue-worker`` agent definition is loaded by
    ``agent.station_orchestrator`` and parameterised with one of the
    fixed teammate roles (``backend`` / ``frontend`` / ``qa``).

    These tests pin down the on-disk contract that the orchestrator
    depends on:

    - ``agent/agents/`` exists and contains ``issue-worker.md``
    - The orchestrator's ``TEAMMATE_ROLES`` constant matches the
      ``backend``/``frontend``/``qa`` trio the lead-agent prompt
      describes.
    """

    EXPECTED_AGENT_FILES = ["issue-worker.md"]
    EXPECTED_TEAMMATE_ROLES = ["backend", "frontend", "qa"]

    def test_agents_directory_exists(self):
        """The agent/agents/ directory must exist."""
        assert _AGENTS_DIR.is_dir(), (
            f"Agents directory missing: {_AGENTS_DIR}. "
            f"station_orchestrator loads SDK agent definitions from this path."
        )

    @pytest.mark.parametrize("filename", EXPECTED_AGENT_FILES)
    def test_required_agent_definition_file_exists(self, filename: str):
        """Each required agent definition file must be present on disk."""
        path = _AGENTS_DIR / filename
        assert path.is_file(), (
            f"Missing agent definition file: {path}. "
            f"station_orchestrator.orchestrate() loads this file at startup."
        )

    def test_issue_worker_definition_has_yaml_front_matter(self):
        """The issue-worker definition must declare its name and model.

        ``load_agent_definition`` in the SDK parses YAML front matter
        and fails closed if name/description are missing.
        """
        content = (_AGENTS_DIR / "issue-worker.md").read_text()
        assert content.startswith("---"), (
            "issue-worker.md must start with YAML front matter (---)"
        )
        # name: issue-worker is what the orchestrator passes through to the SDK
        assert "name: issue-worker" in content, (
            "issue-worker.md front matter must declare `name: issue-worker`"
        )

    def test_teammate_roles_match_orchestrator(self):
        """station_orchestrator.TEAMMATE_ROLES must match the documented trio.

        The lead-agent system prompt and worktree creation both depend
        on these three role names — drift here breaks coordination.
        """
        from agent.station_orchestrator import TEAMMATE_ROLES

        assert list(TEAMMATE_ROLES) == self.EXPECTED_TEAMMATE_ROLES, (
            f"TEAMMATE_ROLES drifted: orchestrator has {list(TEAMMATE_ROLES)}, "
            f"expected {self.EXPECTED_TEAMMATE_ROLES}."
        )
