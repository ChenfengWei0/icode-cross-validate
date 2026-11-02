"""The broken stacks scripts/live_eval.py replays the frozen checks on.

They come from one fixed reference, not from the model's code, so every round is measured against the
same bugs. {a} is the name of the list attribute in the model's code, because alignment scripts often
read it.
"""

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
