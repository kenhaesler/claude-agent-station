"""Tests for the unified diff parser.

Covers:
- Empty/blank input
- Single file addition
- Single file deletion
- Multi-file diff
- Binary files
- File renames
- Hunk parsing with context, additions, and deletions
- Malformed hunk headers
- No newline at end of file marker
"""

from app.services.diff_parser import parse_unified_diff, DiffResult


class TestEmptyInput:
    """parse_unified_diff returns empty DiffResult for empty/blank input."""

    def test_empty_string(self):
        result = parse_unified_diff("")
        assert result == DiffResult()
        assert result.total_files == 0
        assert result.total_additions == 0
        assert result.total_deletions == 0

    def test_whitespace_only(self):
        result = parse_unified_diff("   \n  \n")
        assert result.total_files == 0

    def test_none_like_empty(self):
        result = parse_unified_diff("")
        assert isinstance(result, DiffResult)


class TestSingleFileAddition:
    """Parse a diff that adds a new file."""

    DIFF = """\
diff --git a/src/new_file.py b/src/new_file.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/new_file.py
@@ -0,0 +1,3 @@
+def hello():
+    return "world"
+"""

    def test_file_count(self):
        result = parse_unified_diff(self.DIFF)
        assert result.total_files == 1

    def test_file_is_new(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].is_new is True
        assert result.files[0].is_deleted is False

    def test_filename(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].filename == "src/new_file.py"

    def test_additions_counted(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].additions == 3
        assert result.files[0].deletions == 0
        assert result.total_additions == 3
        assert result.total_deletions == 0

    def test_hunk_structure(self):
        result = parse_unified_diff(self.DIFF)
        assert len(result.files[0].hunks) == 1
        hunk = result.files[0].hunks[0]
        assert hunk.old_start == 0
        assert hunk.old_count == 0
        assert hunk.new_start == 1
        assert hunk.new_count == 3

    def test_line_types(self):
        result = parse_unified_diff(self.DIFF)
        lines = result.files[0].hunks[0].lines
        assert all(line.type == "add" for line in lines)


class TestSingleFileDeletion:
    """Parse a diff that deletes a file."""

    DIFF = """\
diff --git a/src/old_file.py b/src/old_file.py
deleted file mode 100644
index abc1234..0000000
--- a/src/old_file.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old():
-    pass
"""

    def test_file_is_deleted(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].is_deleted is True
        assert result.files[0].is_new is False

    def test_deletions_counted(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].deletions == 2
        assert result.files[0].additions == 0
        assert result.total_deletions == 2


class TestMultiFileDiff:
    """Parse a diff with multiple files changed."""

    DIFF = """\
diff --git a/src/app.py b/src/app.py
index abc1234..def5678 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,7 +10,9 @@ import os

 def main():
-    old_code()
+    new_code()
+    extra_line()

 def helper():
     pass
diff --git a/src/utils.py b/src/utils.py
index 111aaa..222bbb 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,3 +1,4 @@
 def util_a():
     pass
+
+def util_b():
"""

    def test_file_count(self):
        result = parse_unified_diff(self.DIFF)
        assert result.total_files == 2

    def test_filenames(self):
        result = parse_unified_diff(self.DIFF)
        names = [f.filename for f in result.files]
        assert "src/app.py" in names
        assert "src/utils.py" in names

    def test_total_additions_deletions(self):
        result = parse_unified_diff(self.DIFF)
        # app.py: +2 -1, utils.py: +2 -0
        assert result.total_additions == 4
        assert result.total_deletions == 1

    def test_per_file_counts(self):
        result = parse_unified_diff(self.DIFF)
        app_file = next(f for f in result.files if f.filename == "src/app.py")
        assert app_file.additions == 2
        assert app_file.deletions == 1

        utils_file = next(f for f in result.files if f.filename == "src/utils.py")
        assert utils_file.additions == 2
        assert utils_file.deletions == 0


class TestBinaryFile:
    """Parse a diff that includes a binary file."""

    DIFF = """\
diff --git a/assets/logo.png b/assets/logo.png
new file mode 100644
index 0000000..abc1234
Binary files /dev/null and b/assets/logo.png differ
"""

    def test_binary_detected(self):
        result = parse_unified_diff(self.DIFF)
        assert result.total_files == 1
        assert result.files[0].is_binary is True
        assert result.files[0].filename == "assets/logo.png"

    def test_no_hunks_for_binary(self):
        result = parse_unified_diff(self.DIFF)
        assert len(result.files[0].hunks) == 0


class TestFileRename:
    """Parse a diff with a file rename."""

    DIFF = """\
diff --git a/old_name.py b/new_name.py
similarity index 95%
rename from old_name.py
rename to new_name.py
index abc1234..def5678 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1,3 +1,3 @@
 def func():
-    return "old"
+    return "new"
"""

    def test_rename_detected(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].old_filename == "old_name.py"
        assert result.files[0].filename == "new_name.py"


class TestContextLines:
    """Verify context lines have both old and new line numbers."""

    DIFF = """\
diff --git a/file.py b/file.py
index abc..def 100644
--- a/file.py
+++ b/file.py
@@ -5,5 +5,6 @@ def foo():
     a = 1
     b = 2
+    c = 3
     d = 4
     e = 5
"""

    def test_context_line_numbers(self):
        result = parse_unified_diff(self.DIFF)
        hunk = result.files[0].hunks[0]
        # First line is context " a = 1" at old=5, new=5
        ctx = [l for l in hunk.lines if l.type == "context"]
        assert len(ctx) == 5  # a, b, d, e, trailing empty line
        assert ctx[0].old_line == 5
        assert ctx[0].new_line == 5

    def test_add_line_number(self):
        result = parse_unified_diff(self.DIFF)
        hunk = result.files[0].hunks[0]
        add_lines = [l for l in hunk.lines if l.type == "add"]
        assert len(add_lines) == 1
        assert add_lines[0].new_line == 7  # after b=2 at line 6


class TestNoNewlineMarker:
    """The '\\ No newline at end of file' marker should be ignored."""

    DIFF = """\
diff --git a/file.txt b/file.txt
index abc..def 100644
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old content
\\ No newline at end of file
+new content
\\ No newline at end of file
"""

    def test_marker_not_counted(self):
        result = parse_unified_diff(self.DIFF)
        assert result.total_additions == 1
        assert result.total_deletions == 1
        # No lines of type 'context' from the backslash marker
        lines = result.files[0].hunks[0].lines
        for line in lines:
            assert "\\ No newline" not in line.content


class TestMultipleHunks:
    """A file with multiple hunks."""

    DIFF = """\
diff --git a/big_file.py b/big_file.py
index abc..def 100644
--- a/big_file.py
+++ b/big_file.py
@@ -10,3 +10,4 @@ def first():
     pass
+    # added in first hunk
     end_first()
@@ -50,3 +51,4 @@ def second():
     pass
+    # added in second hunk
     end_second()
"""

    def test_two_hunks(self):
        result = parse_unified_diff(self.DIFF)
        assert len(result.files[0].hunks) == 2

    def test_hunk_offsets(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].hunks[0].old_start == 10
        assert result.files[0].hunks[1].old_start == 50

    def test_total_additions(self):
        result = parse_unified_diff(self.DIFF)
        assert result.files[0].additions == 2


class TestMalformedHunkHeader:
    """Malformed hunk headers should be skipped gracefully."""

    DIFF = """\
diff --git a/file.py b/file.py
index abc..def 100644
--- a/file.py
+++ b/file.py
@@ MALFORMED HEADER @@
+this line should be ignored since hunk is None
"""

    def test_no_crash(self):
        result = parse_unified_diff(self.DIFF)
        assert result.total_files == 1
        # The malformed hunk should be skipped
        assert len(result.files[0].hunks) == 0


class TestSingleLineHunk:
    """Hunk header without count (e.g., @@ -1 +1 @@) defaults count to 1."""

    DIFF = """\
diff --git a/one.txt b/one.txt
index abc..def 100644
--- a/one.txt
+++ b/one.txt
@@ -1 +1 @@
-old
+new
"""

    def test_single_line_counts(self):
        result = parse_unified_diff(self.DIFF)
        hunk = result.files[0].hunks[0]
        assert hunk.old_count == 1
        assert hunk.new_count == 1
        assert result.total_additions == 1
        assert result.total_deletions == 1
