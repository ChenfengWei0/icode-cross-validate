"""The broken stacks scripts/live_eval.py replays the frozen checks on.

They come from one fixed reference, not from the model's code, so every round is measured against the
same bugs. {a} is the name of the list attribute in the model's code, because alignment scripts often
read it.
"""

import re

REFERENCE = """class Stack:
    def __init__(self):
        self.{a} = []

    def push(self, x):
        self.{a}.append(x)

    def pop(self):
        if not self.{a}:
            raise IndexError("pop from empty stack")
        return self.{a}.pop()

    def size(self):
        return len(self.{a})
"""

_PUSH = "        self.{a}.append(x)"
_POP = "        return self.{a}.pop()"
_RAISE = '            raise IndexError("pop from empty stack")'
_SIZE = "        return len(self.{a})"

_EDITS = {
    "fifo_pop0": (_POP, "        return self.{a}.pop(0)"),
    "fifo_insert": (_PUSH, "        self.{a}.insert(0, x)"),
    "second_last": (_POP, "        return self.{a}.pop(-2 if len(self.{a}) > 1 else -1)"),
    "peek_no_remove": (_POP, "        return self.{a}[-1]"),
    "pop_returns_none": (_POP, "        self.{a}.pop()"),
    "empty_returns_none": (_RAISE, "            return None"),
    "empty_valueerror": (_RAISE, '            raise ValueError("empty")'),
    "empty_keyerror": (_RAISE, '            raise KeyError("empty")'),
    "size_plus_one": (_SIZE, "        return len(self.{a}) + 1"),
    "size_always_0": (_SIZE, "        return 0"),
    "size_renamed": ("def size(self)", "def length(self)"),
    "pop_renamed": ("def pop(self)", "def remove(self)"),
    "drop_after_5": (_PUSH, "        if len(self.{a}) < 5:\n            self.{a}.append(x)"),
    "drop_after_30": (_PUSH, "        if len(self.{a}) < 30:\n            self.{a}.append(x)"),
    "drop_duplicates": (_PUSH, "        if x not in self.{a}:\n            self.{a}.append(x)"),
}


def source(name: str, attribute: str) -> str:
    """The source of one mutant, with the given name for the list attribute."""
    old, new = _EDITS[name]
    return REFERENCE.replace(old, new).replace("{a}", attribute)


NAMES = tuple(_EDITS)
TARGET = "stack.py"
_ATTRIBUTE = re.compile(r"self\.(\w+)\s*(?::[^=\n]+)?=\s*(?:\[\]|list\(\))")


def attribute_of(code: str) -> str | None:
    match = _ATTRIBUTE.search(code)
    return match.group(1) if match else None


def prepare(code: str) -> dict[str, str] | None:
    """Every mutant's source for this round's code, or None when they cannot be made from it."""
    attribute = attribute_of(code)
    return {name: source(name, attribute) for name in NAMES} if attribute else None
