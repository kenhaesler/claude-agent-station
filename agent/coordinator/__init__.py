"""Live-coordinating manager: real-time employee orchestration with task splitting.

Submodules:
  - decide: CLI decision gateway (select-mode, check-confidence, etc.)
  - mode_selector: LLM-based complexity routing
  - modes: Mode registry and escalation ladder
"""
