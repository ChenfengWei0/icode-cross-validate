"""Structure check written by the alignment agent: some element of the code represents the snake's length."""

import os
import re
import sys

with open(os.path.join(os.environ["CV_W"], "snake.py"), encoding="utf-8") as f:
    source = f.read()
if re.search(r"self\.body\s*=", source):
    sys.exit(0)
print("snake.py 中没有找到表示蛇的长度的元素（Snake.body）")
sys.exit(1)
