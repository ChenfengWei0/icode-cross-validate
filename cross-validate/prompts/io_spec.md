你是交叉验证的子任务 agent。另一个 agent 会按下面的需求写一个程序：从标准输入读入，向标准输出写出。你看不到那个程序，也不要去写它。你的任务是只根据需求写一个检查模块，用来判断任意一个输出是不是需求接受的输出。

需求常常允许多种正确输出。检查只核对输出是否满足需求写明的条件，不自己求出一个答案再去比较。你不需要知道怎样构造答案；在小输入上可以用穷举来核对条件。

# 模块的内容

一个 Python 文件，只用标准库，不读写文件，不打印。必须定义：

```
CONDITIONS = [{"id": "C1", "text": "对输出的一条要求", "quote": "需求中的原文片段"}, ...]
INPUT_RULES = [{"id": "I1", "text": "对输入的一条约束或保证", "quote": "需求中的原文片段"}, ...]

def valid_input(text: str) -> list[str]:            # 输入违反的 INPUT_RULES 编号；合法时返回 []
def generate(rng: random.Random, scale: int) -> str:  # 生成一个合法的小输入，scale 从 1 到 10
def check(input_text: str, output_text: str) -> list[tuple[str, str]]:  # 输出违反的 (CONDITIONS 编号, 具体原因)；没有违反时返回 []
```

# 要求

1. **每条要求有原文。** `quote` 从需求中逐字复制，可以只取一个片段，公式记号照抄。一条只说一件事。输出格式（行数、每行几个数、取值范围）也写成条件。
2. **只查需求写明的。** 需求没有规定的，不查，一律接受：例如需求说"输出任意一个"，就不要求某一个特定答案；需求没说顺序，就不查顺序。大小写、空白按需求的说法处理；没有说时，按空白切分后逐个比较，不在意多余的空格和换行。
3. **不要抛出异常。** 输出格式不对（数不够、不是整数、多出内容）时返回对应格式条件的违反，不要让 `check` 出错。
4. **原因要具体。** 第二项写出在哪里不满足，例如"第 2 组：第 3 个数 7 超出 [1, 5]"。
5. **输入要合法、要小、要有边界。** `valid_input` 检查需求中输入部分的每条约束和保证（"保证……"也算）。`generate` 只生成满足它们的输入：难以直接构造时，先随机生成，再用同样的判断筛掉不合法的。scale 为 1 时取最小的规模；scale 越大规模越大，但 scale 为 10 时也要让 `check` 在一秒内完成，整个输入不超过 20000 个字符。要覆盖边界：最小的规模、相同的元素、取值的上下界附近。输入有多组时，可以在一个输入中放几组。
6. **最优和无解用穷举判定。** 需求要求最少、最多或最优，或者要求在无解时输出某个标记时，`check` 要在输入足够小时枚举所有可能的答案（或者所有候选），由枚举的结果判定最优值、判定有没有解；不要用你推导出的公式、贪心或结论代替枚举。写程序的 agent 很可能推导出同一个公式，公式错了两边会一起错，检查就失去了作用。输入太大、无法在一秒内枚举时，这一条不查（不返回违反）；所以 `generate` 要生成足够多能够枚举的小输入。
7. **示例必须通过。** 需求中给出的示例输出一定是正确的输出，`check` 必须接受它，`valid_input` 必须接受示例输入；空输出必须被判为违反。程序会先用这几点检查你的模块，不通过时会把问题发回给你。

# 一个例子

需求："给定 n（2 ≤ n ≤ 100），输出两个不同的整数 a、b，满足 1 ≤ a, b ≤ n 且 a + b 是偶数；n = 2 时输出 -1。"

```python
import random

CONDITIONS = [
    {"id": "C1", "text": "n = 2 时输出 -1", "quote": "n = 2 时输出 -1"},
    {"id": "C2", "text": "n > 2 时输出两个整数", "quote": "输出两个不同的整数 a、b"},
    {"id": "C3", "text": "a、b 不同且都在 1 到 n 之间", "quote": "两个不同的整数 a、b，满足 1 ≤ a, b ≤ n"},
    {"id": "C4", "text": "a + b 是偶数", "quote": "a + b 是偶数"},
]
INPUT_RULES = [{"id": "I1", "text": "一个整数 n，2 ≤ n ≤ 100", "quote": "给定 n（2 ≤ n ≤ 100）"}]


def valid_input(text):
    parts = text.split()
    if len(parts) != 1 or not parts[0].lstrip("-").isdigit() or not 2 <= int(parts[0]) <= 100:
        return ["I1"]
    return []


def generate(rng, scale):
    n = 2 if scale == 1 else rng.choice([3, 4, rng.randint(2, 10 * scale)])
    return f"{n}\n"


def check(input_text, output_text):
    n = int(input_text.split()[0])
    out = output_text.split()
    if n == 2:
        return [] if out == ["-1"] else [("C1", f"n = 2 时输出了 {' '.join(out)[:50]}")]
    if len(out) != 2 or not all(x.lstrip("-").isdigit() for x in out):
        return [("C2", f"输出不是两个整数：{' '.join(out)[:50]}")]
    a, b = map(int, out)
    bad = []
    if a == b or not (1 <= a <= n and 1 <= b <= n):
        bad.append(("C3", f"a = {a}，b = {b}，n = {n}"))
    if (a + b) % 2:
        bad.append(("C4", f"{a} + {b} = {a + b} 是奇数"))
    return bad
```

# 回答格式

只输出一个 ```python 代码块，里面是完整的模块。

# 需求

{{REQUIREMENT}}
