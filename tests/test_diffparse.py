from reviewbot.diffparse import parse_patch, snap_to_commentable, split_git_diff

PATCH = """@@ -10,6 +10,8 @@ def f():
 a = 1
 b = 2
-c = 3
+c = compute()
+d = 4
 e = 5
 f = 6
"""


def test_added_and_context_lines():
    p = parse_patch(PATCH)
    # new-file numbering starts at 10: a=10, b=11, (removed c), c=12, d=13, e=14, f=15
    assert p.added_lines == {12, 13}
    assert p.commentable_lines == {10, 11, 12, 13, 14, 15}


def test_numbered_output_shows_line_numbers_not_for_removed():
    p = parse_patch(PATCH)
    assert "   12 | +c = compute()" in p.numbered
    assert "      | -c = 3" in p.numbered


def test_snap_exact_nearby_and_far():
    p = parse_patch(PATCH)
    assert snap_to_commentable(12, p) == 12
    assert snap_to_commentable(16, p) == 13  # within 3 of an added line
    assert snap_to_commentable(40, p) is None


def test_multiple_hunks_reset_numbering():
    patch = "@@ -1,2 +1,2 @@\n x\n+y\n@@ -50,2 +50,3 @@\n z\n+w\n v\n"
    p = parse_patch(patch)
    assert p.added_lines == {2, 51}


def test_no_newline_marker_is_ignored():
    patch = "@@ -1,1 +1,1 @@\n-old\n+new\n\\ No newline at end of file\n"
    p = parse_patch(patch)
    assert p.added_lines == {1}


GIT_DIFF = """diff --git a/app.py b/app.py
index 111..222 100644
--- a/app.py
+++ b/app.py
@@ -1,2 +1,3 @@
 import os
+import sys
 print(1)
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/logo.png b/logo.png
Binary files differ
"""


def test_split_git_diff_skips_deleted_and_binary():
    files = split_git_diff(GIT_DIFF)
    assert list(files) == ["app.py"]
    assert files["app.py"].startswith("@@ -1,2 +1,3 @@")
