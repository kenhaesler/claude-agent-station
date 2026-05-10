import { describe, it, expect } from 'vitest';
import { splitHelpSection } from './help-content';

describe('splitHelpSection', () => {
  it('extracts TL;DR from a leading blockquote', () => {
    const md = '> **TL;DR** — A run takes an issue from picked-up to merged.\n\nBody text here.';
    const result = splitHelpSection(md);
    expect(result.tldr).toBe('A run takes an issue from picked-up to merged.');
    expect(result.howItWorks.trim()).toBe('Body text here.');
    expect(result.underTheHood).toBeNull();
  });

  it('splits how-it-works and under-the-hood on the marker', () => {
    const md = [
      '> **TL;DR** — Lead, teammates, manager.',
      '',
      'How it works body.',
      '',
      '<!-- under-the-hood -->',
      '',
      'Deep technical bits.',
    ].join('\n');
    const result = splitHelpSection(md);
    expect(result.tldr).toBe('Lead, teammates, manager.');
    expect(result.howItWorks.trim()).toBe('How it works body.');
    expect(result.underTheHood?.trim()).toBe('Deep technical bits.');
  });

  it('treats missing TL;DR as null', () => {
    const md = 'Just plain prose, no blockquote.';
    const result = splitHelpSection(md);
    expect(result.tldr).toBeNull();
    expect(result.howItWorks.trim()).toBe('Just plain prose, no blockquote.');
    expect(result.underTheHood).toBeNull();
  });

  it('strips a "TL;DR —" prefix variant', () => {
    const md = '> TL;DR — short summary.\n\nBody.';
    const result = splitHelpSection(md);
    expect(result.tldr).toBe('short summary.');
  });

  it('keeps fenced mermaid blocks intact in the body', () => {
    const md = [
      '> **TL;DR** — flow.',
      '',
      'Intro.',
      '',
      '```mermaid',
      'flowchart TD',
      '  A --> B',
      '```',
      '',
      'Outro.',
    ].join('\n');
    const result = splitHelpSection(md);
    expect(result.howItWorks).toContain('```mermaid');
    expect(result.howItWorks).toContain('flowchart TD');
  });
});
