"""Parse unified git diff output into structured JSON data."""


from pydantic import BaseModel


class DiffLine(BaseModel):
    """A single line in a diff hunk."""
    type: str  # 'add', 'remove', 'context'
    content: str
    old_line: int | None = None
    new_line: int | None = None


class DiffHunk(BaseModel):
    """A contiguous block of changes in a file."""
    header: str  # e.g., "@@ -10,7 +10,9 @@ function foo()"
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[DiffLine]


class DiffFile(BaseModel):
    """A single file's diff data."""
    filename: str
    old_filename: str | None = None  # For renames
    additions: int = 0
    deletions: int = 0
    is_new: bool = False
    is_deleted: bool = False
    is_binary: bool = False
    hunks: list[DiffHunk] = []


class DiffResult(BaseModel):
    """Complete parsed diff result."""
    files: list[DiffFile] = []
    total_additions: int = 0
    total_deletions: int = 0
    total_files: int = 0


def parse_unified_diff(diff_text: str) -> DiffResult:
    """Parse a unified diff string into structured DiffResult.

    Args:
        diff_text: Raw output from `git diff`.

    Returns:
        DiffResult with parsed file diffs, hunks, and line data.
    """
    if not diff_text or not diff_text.strip():
        return DiffResult()

    files: list[DiffFile] = []
    current_file: DiffFile | None = None
    current_hunk: DiffHunk | None = None
    old_line = 0
    new_line = 0

    lines = diff_text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        # New file diff header: "diff --git a/path b/path"
        if line.startswith('diff --git '):
            # Save previous file
            if current_file is not None:
                if current_hunk is not None:
                    current_file.hunks.append(current_hunk)
                files.append(current_file)

            # Extract filename from "diff --git a/foo b/foo"
            parts = line.split(' b/', 1)
            filename = parts[1] if len(parts) > 1 else line.split()[-1]

            current_file = DiffFile(filename=filename)
            current_hunk = None
            i += 1
            continue

        # Handle file metadata lines
        if current_file is not None:
            if line.startswith('new file mode'):
                current_file.is_new = True
                i += 1
                continue
            if line.startswith('deleted file mode'):
                current_file.is_deleted = True
                i += 1
                continue
            if line.startswith('Binary files'):
                current_file.is_binary = True
                i += 1
                continue
            if line.startswith('rename from '):
                current_file.old_filename = line[len('rename from '):]
                i += 1
                continue
            if line.startswith('--- '):
                # Old filename header, skip
                i += 1
                continue
            if line.startswith('+++ '):
                # New filename header, skip
                i += 1
                continue
            if line.startswith('index ') or line.startswith('similarity index') or line.startswith('rename to '):
                i += 1
                continue

        # Hunk header: "@@ -old_start,old_count +new_start,new_count @@ context"
        if line.startswith('@@') and current_file is not None:
            if current_hunk is not None:
                current_file.hunks.append(current_hunk)

            # Parse hunk header
            try:
                header_end = line.index('@@', 2)
                header_content = line[3:header_end].strip()
                parts = header_content.split()

                # Parse old range: -start,count or -start
                old_part = parts[0][1:]  # Remove '-'
                if ',' in old_part:
                    os, oc = old_part.split(',')
                    old_start, old_count = int(os), int(oc)
                else:
                    old_start, old_count = int(old_part), 1

                # Parse new range: +start,count or +start
                new_part = parts[1][1:]  # Remove '+'
                if ',' in new_part:
                    ns, nc = new_part.split(',')
                    new_start, new_count = int(ns), int(nc)
                else:
                    new_start, new_count = int(new_part), 1

                current_hunk = DiffHunk(
                    header=line,
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    lines=[],
                )
                old_line = old_start
                new_line = new_start
            except (ValueError, IndexError):
                # Malformed hunk header, skip
                current_hunk = None

            i += 1
            continue

        # Diff content lines
        if current_hunk is not None:
            if line.startswith('+'):
                current_hunk.lines.append(DiffLine(
                    type='add',
                    content=line[1:],
                    new_line=new_line,
                ))
                if current_file:
                    current_file.additions += 1
                new_line += 1
            elif line.startswith('-'):
                current_hunk.lines.append(DiffLine(
                    type='remove',
                    content=line[1:],
                    old_line=old_line,
                ))
                if current_file:
                    current_file.deletions += 1
                old_line += 1
            elif line.startswith(' ') or line == '':
                # Context line (or empty line within hunk)
                content = line[1:] if line.startswith(' ') else ''
                current_hunk.lines.append(DiffLine(
                    type='context',
                    content=content,
                    old_line=old_line,
                    new_line=new_line,
                ))
                old_line += 1
                new_line += 1
            elif line.startswith('\\'):
                # "\ No newline at end of file" - skip
                pass

        i += 1

    # Save last file
    if current_file is not None:
        if current_hunk is not None:
            current_file.hunks.append(current_hunk)
        files.append(current_file)

    total_additions = sum(f.additions for f in files)
    total_deletions = sum(f.deletions for f in files)

    return DiffResult(
        files=files,
        total_additions=total_additions,
        total_deletions=total_deletions,
        total_files=len(files),
    )
