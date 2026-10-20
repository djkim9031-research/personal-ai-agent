#!/bin/sh
# Creates the small Python project the twenty-step harness test works on, as a git
# repository at the given path. One test fails on purpose: unique_words is case-sensitive.
set -eu
dir=${1:?usage: make-test-repo.sh DIR}
rm -rf "$dir"
mkdir -p "$dir/textstats" "$dir/tests"
cd "$dir"
cat > README.md <<'TXT'
# textstats

Counts words, characters and unique words in a text file.

    python3 -m textstats.cli FILE
TXT
cat > pyproject.toml <<'TXT'
[project]
name = "textstats"
version = "0.1.0"
requires-python = ">=3.10"
TXT
cat > textstats/__init__.py <<'TXT'
from .count import char_count, unique_words, word_count
TXT
cat > textstats/count.py <<'TXT'
def word_count(text):
    return len(text.split())


def char_count(text, spaces=True):
    if spaces:
        return len(text)
    return len(text.replace(" ", ""))


def unique_words(text):
    return len(set(text.split()))
TXT
cat > textstats/cli.py <<'TXT'
import argparse

from .count import char_count, unique_words, word_count


def main():
    parser = argparse.ArgumentParser(description="Counts words and characters in a file.")
    parser.add_argument("file")
    args = parser.parse_args()
    with open(args.file) as f:
        text = f.read()
    print(f"words: {word_count(text)}")
    print(f"characters: {char_count(text)}")
    print(f"unique: {unique_words(text)}")


if __name__ == "__main__":
    main()
TXT
cat > tests/__init__.py <<'TXT'
TXT
cat > tests/test_count.py <<'TXT'
import unittest

from textstats import char_count, unique_words, word_count


class CountTests(unittest.TestCase):
    def test_word_count(self):
        self.assertEqual(word_count("one two three"), 3)
        self.assertEqual(word_count(""), 0)

    def test_char_count(self):
        self.assertEqual(char_count("ab cd"), 5)
        self.assertEqual(char_count("ab cd", spaces=False), 4)

    def test_unique_words(self):
        self.assertEqual(unique_words("a b a"), 2)

    def test_unique_words_ignores_case(self):
        self.assertEqual(unique_words("The the THE"), 1)


if __name__ == "__main__":
    unittest.main()
TXT
git init -q
git config user.name "textstats test"
git config user.email "test@example.invalid"
git add -A
git commit -qm "Start"
echo "test project at $dir"
