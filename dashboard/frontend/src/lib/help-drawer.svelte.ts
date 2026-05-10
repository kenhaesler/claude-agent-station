// ============================================
// Help Drawer — global state for the contextual ? drawer
// ============================================

export const helpDrawer = $state<{ openSection: string | null }>({ openSection: null });

export function openHelpDrawer(section: string): void {
  helpDrawer.openSection = section;
}

export function closeHelpDrawer(): void {
  helpDrawer.openSection = null;
}
