// ============================================
// Help Sections — single source of truth for the eight help-page section
// ids and their human-readable titles. Used by both HelpPage (full /help
// route) and HelpDrawer (contextual ? drawer) to keep them in lockstep.
// ============================================

export interface HelpSection {
  id: string;
  title: string;
}

export const HELP_SECTIONS: readonly HelpSection[] = [
  { id: 'run-lifecycle', title: 'Run lifecycle' },
  { id: 'roles', title: 'The three roles' },
  { id: 'verdicts', title: 'Verdicts' },
  { id: 'eligibility', title: 'Issue eligibility' },
  { id: 'throttling', title: 'Plan-tier throttling' },
  { id: 'plans-worktrees', title: 'Plans & worktrees' },
  { id: 'pages-tour', title: 'Page-by-page tour' },
  { id: 'troubleshooting', title: 'Troubleshooting' },
] as const;

export const HELP_SECTION_TITLES: Record<string, string> = Object.fromEntries(
  HELP_SECTIONS.map((s) => [s.id, s.title]),
);
