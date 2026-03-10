/**
 * Parses Claude CLI JSONL stream events into structured, renderable objects.
 */

export type LogEventType = 'system_init' | 'assistant_text' | 'assistant_thinking' | 'assistant_tool_use' | 'tool_result' | 'result' | 'rate_limit' | 'unknown';

export interface ParsedLogEvent {
  type: LogEventType;
  timestamp?: string;
  sessionId?: string;
  raw: string;

  // system_init
  cwd?: string;
  tools?: string[];

  // assistant_text
  text?: string;

  // assistant_thinking
  thinking?: string;

  // assistant_tool_use
  toolName?: string;
  toolInput?: unknown;

  // tool_result
  toolResultContent?: string;
  isError?: boolean;

  // result
  resultStatus?: string;
  costUsd?: number;
  numTurns?: number;
  durationMs?: number;
  model?: string;
}

export function parseLogLine(line: string): ParsedLogEvent | ParsedLogEvent[] | null {
  if (!line.trim()) return null;

  let json: any;
  try {
    json = JSON.parse(line);
  } catch {
    // Not JSON — return as plain text
    return {
      type: 'unknown',
      text: line,
      raw: line,
    };
  }

  const base = {
    sessionId: json.session_id,
    raw: line,
  };

  switch (json.type) {
    case 'system': {
      if (json.subtype === 'init') {
        return {
          ...base,
          type: 'system_init',
          cwd: json.cwd,
          tools: json.tools?.slice(0, 20), // limit for display
        };
      }
      return { ...base, type: 'unknown', text: line };
    }

    case 'assistant': {
      const msg = json.message;
      if (!msg?.content) return { ...base, type: 'unknown', text: line };

      const events: ParsedLogEvent[] = [];
      for (const block of msg.content) {
        if (block.type === 'text') {
          events.push({
            ...base,
            type: 'assistant_text',
            text: block.text,
            model: msg.model,
          });
        } else if (block.type === 'thinking') {
          events.push({
            ...base,
            type: 'assistant_thinking',
            thinking: block.thinking,
          });
        } else if (block.type === 'tool_use') {
          events.push({
            ...base,
            type: 'assistant_tool_use',
            toolName: block.name,
            toolInput: block.input,
          });
        }
      }
      return events.length > 0 ? events : { ...base, type: 'unknown', text: line };
    }

    case 'user': {
      const msg = json.message;
      if (!msg?.content) return { ...base, type: 'unknown', text: line };

      const events: ParsedLogEvent[] = [];
      for (const block of msg.content) {
        if (block.type === 'tool_result') {
          let content = '';
          let isError = block.is_error === true;
          if (typeof block.content === 'string') {
            content = block.content;
          } else if (Array.isArray(block.content)) {
            content = block.content
              .filter((c: any) => c.type === 'text')
              .map((c: any) => c.text)
              .join('\n');
          }
          events.push({
            ...base,
            type: 'tool_result',
            toolResultContent: content,
            isError,
          });
        }
      }
      return events.length > 0 ? events : null; // skip non-tool-result user messages
    }

    case 'result': {
      return {
        ...base,
        type: 'result',
        resultStatus: json.subtype || 'completed',
        costUsd: json.total_cost_usd,
        numTurns: json.num_turns,
        durationMs: json.duration_ms,
        model: json.modelUsage ? Object.keys(json.modelUsage)[0] : undefined,
      };
    }

    case 'rate_limit_event':
      return {
        ...base,
        type: 'rate_limit',
      };

    default:
      return { ...base, type: 'unknown', text: line };
  }
}

/** Truncate text with ellipsis */
export function truncate(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text;
  return text.slice(0, maxLen) + '…';
}

/** Format tool input for display — show key params, not the whole object */
export function formatToolInput(toolName: string, input: unknown): string {
  if (!input || typeof input !== 'object') return String(input ?? '');
  const obj = input as Record<string, unknown>;

  switch (toolName) {
    case 'Read':
      return String(obj.file_path || '');
    case 'Write':
      return String(obj.file_path || '');
    case 'Edit':
      return String(obj.file_path || '');
    case 'Glob':
      return `${obj.pattern || ''}${obj.path ? ' in ' + obj.path : ''}`;
    case 'Grep':
      return `/${obj.pattern || ''}/` + (obj.path ? ` in ${obj.path}` : '') + (obj.glob ? ` (${obj.glob})` : '');
    case 'Bash':
      return truncate(String(obj.command || ''), 200);
    case 'Agent':
      return String(obj.description || '');
    case 'WebFetch':
      return String(obj.url || '');
    case 'WebSearch':
      return String(obj.query || '');
    case 'TodoWrite':
      return `${(obj.todos as any[])?.length || 0} items`;
    case 'Skill':
      return String(obj.skill || '');
    default:
      return truncate(JSON.stringify(input), 200);
  }
}
