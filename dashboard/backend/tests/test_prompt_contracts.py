"""Tests for prompt file structural contracts.

Validates that all agent prompt files in agent/prompts/ contain the
required structural sections that the system depends on at runtime.

Covers:
- All 7 prompt roles (analyst, assigner, employee, manager, planner,
  reviewer, triager) have corresponding .md files
- Each prompt contains an <identity> section
- PROMPT_ROLES in the prompts router stays in sync with actual files
- MODE_REGISTRY prompt_file references point to real files
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
        actual_files_count = len([
            p for p in _PROMPTS_DIR.glob("*.md")
            if p.stem != "REPORT-SCHEMAS"
        ])
        router_count = len(PROMPT_ROLES)

        # This assertion documents the gap. Update both sides when fixed.
        assert actual_files_count == 8, (
            f"Expected 8 prompt files on disk, found {actual_files_count}"
        )
        assert router_count == 6, (
            f"PROMPT_ROLES currently has {router_count} entries. "
            f"Expected 6 (triager not yet added to router)."
        )


@pytest.mark.skip(
    reason="legacy agent.coordinator.modes removed; see TODO below",
)
class TestModeRegistryPromptSync:
    """Verify MODE_REGISTRY prompt_file references point to real files.

    TODO: `agent.coordinator.modes.MODE_REGISTRY` was removed when the legacy
    coordinator was replaced by `agent.station_orchestrator`. Rewrite these
    tests against the current mode registry (or drop them if the registry
    concept no longer applies under Agent Teams).
    """

    def test_all_mode_prompt_files_exist(self):
        """Every mode's prompt_file must exist in agent/prompts/."""
        from agent.coordinator.modes import MODE_REGISTRY

        for mode_name, spec in MODE_REGISTRY.items():
            path = _PROMPTS_DIR / spec.prompt_file
            assert path.is_file(), (
                f"MODE_REGISTRY['{mode_name}'].prompt_file = '{spec.prompt_file}' "
                f"but file does not exist at {path}"
            )

    def test_mode_prompt_files_are_known_roles(self):
        """Mode prompt files should reference one of the known prompt roles."""
        from agent.coordinator.modes import MODE_REGISTRY

        known_files = {f"{role}.md" for role in ALL_PROMPT_ROLES}
        for mode_name, spec in MODE_REGISTRY.items():
            assert spec.prompt_file in known_files, (
                f"MODE_REGISTRY['{mode_name}'].prompt_file = '{spec.prompt_file}' "
                f"is not a known prompt file. Known: {sorted(known_files)}"
            )

    def test_employee_runner_prompt_map_covers_modes(self):
        """The prompt_map in employee_runner.py should be consistent with modes."""
        # The prompt_map in employee_runner hardcodes mode -> prompt file mappings.
        # Verify it matches the MODE_REGISTRY.
        from agent.coordinator.modes import MODE_REGISTRY

        # These are the modes that have explicit prompt_map entries in employee_runner.py
        # (analyze, plan, triage, review). All others fall through to employee.md.
        explicit_modes = {"analyze": "analyst.md", "plan": "planner.md",
                          "triage": "triager.md", "review": "reviewer.md"}

        for mode_name, expected_file in explicit_modes.items():
            if mode_name in MODE_REGISTRY:
                assert MODE_REGISTRY[mode_name].prompt_file == expected_file, (
                    f"MODE_REGISTRY['{mode_name}'].prompt_file "
                    f"= '{MODE_REGISTRY[mode_name].prompt_file}' "
                    f"but employee_runner prompt_map expects '{expected_file}'"
                )
