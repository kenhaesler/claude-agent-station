// ============================================
// Help Content — parse a section markdown file into TL;DR, how-it-works,
// and under-the-hood parts. Pure functions, fully unit-tested.
// ============================================

export interface HelpSectionParts {
  tldr: string | null;
  howItWorks: string;
  underTheHood: string | null;
}

const UNDER_THE_HOOD_MARKER = '<!-- under-the-hood -->';

// Matches a leading blockquote line that opens with `**TL;DR**` or `TL;DR`.
// Captures the prose after the em-dash (or hyphen) separator.
const TLDR_RE = /^>\s*(?:\*\*TL;DR\*\*|TL;DR)\s*[—-]\s*(.+?)\s*$/m;

export function splitHelpSection(source: string): HelpSectionParts {
  const markerIdx = source.indexOf(UNDER_THE_HOOD_MARKER);
  let body: string;
  let underTheHood: string | null;

  if (markerIdx === -1) {
    body = source;
    underTheHood = null;
  } else {
    body = source.slice(0, markerIdx);
    underTheHood = source.slice(markerIdx + UNDER_THE_HOOD_MARKER.length);
  }

  const tldrMatch = body.match(TLDR_RE);
  const tldr = tldrMatch ? tldrMatch[1].trim() : null;

  const howItWorks = tldrMatch
    ? body.replace(tldrMatch[0], '').replace(/^\s*\n+/, '')
    : body;

  return { tldr, howItWorks, underTheHood };
}
