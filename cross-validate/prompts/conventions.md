## 为每一段选择形态

按下面的顺序选择，前一种不可行才用后一种：

1. **形式化性质**（`"form": "property"`）：只判断结果是否满足一条性质，不必把结果算出来。可行的条件是：这段要求的每个词都能对应到接口里的符号或基本运算（定宽整数、数组、结构体、指针等），并且能写成下面四类之一：
   - `P1`：单次调用的前后条件；
   - `P2`：任意操作序列之后都成立的不变量；
   - `P3`：几次调用结果之间的关系，如往返、单调；
   - `P4`：代码结构上的约束，如谁能写某个字段。
2. **简化参考模型**（`"form": "reference_model"`）：写不成性质，但存在一种明显正确、只是很慢的做法（如暴力枚举）。
3. **具体用例**（`"form": "cases"`）：以上两种都不行，但能对若干具体输入，直接从需求算出期望结果。

三种都不可行时写 `{"item": ..., "segment": ..., "form": "uncovered", "reason": "<原因>"}`。例如"要及时处理"中的"及时"没有确定的真假条件。

## 必须遵守

- 只陈述性质，不重写实现；产物要比实现简单得多。
- 只用头文件里声明的函数和类型。验证代码自己记账：不要调用被测代码提供的汇总、查询函数来充当检查依据（可以直接读结构体字段）。
- 调用顺序和参数范围只取需求与头文件**写明**的前置条件。没写明的不要自行假设。
- 取值范围从原文或接口类型推不出来时，不要取类型的全部范围；把每种合理的范围各写成一个产物（同一段可以给出多个产物），用 `reading` 区分。
- `reading`：一句话说明这个产物采用的读法。同一读法需要几条互相补充的检查时（例如一条结构约束加一条序列不变量），给它们相同的 `reading`；不同读法用不同的 `reading`。
- `functions`：这个产物检查的接口函数。

## 形式化性质（P1–P3）的写法

给出两段 C 代码：`spec` 和 `harness`。头文件、`<assert.h>`、`<stdbool.h>`、`<stdint.h>` 以及下面的非确定性接口已经声明好，不必再写：

```c
int  __VERIFIER_nondet_int(void);   /* 另有 bool char uchar short ushort uint unsigned long ulong longlong ulonglong size_t float double */
void __VERIFIER_assume(int cond);   /* 只考虑满足 cond 的执行 */
```

`spec` 定义规范谓词（不能调用任何接口函数；辅助函数一律写成 `static`；规范谓词本身不要写 `static`）：

- `int cv_pre_<f>(<f 的参数>)`：头文件或需求写明的前置条件（有才写）。
- P1：`int cv_post_<f>(<f 的参数>, <返回值>)`，用于参数与返回值都是标量的函数；返回值为 void 时省略该参数。
- P2：`int cv_inv_<T>(const T *s)`，记录类型 `T` 上的状态不变量。
- P3：`int cv_rel_<名字>(...)`，几次调用结果之间的关系。

两个来源会各自独立写出 spec，判定器靠这些固定名字和签名比较两种读法，所以请严格使用上面的名字和参数顺序。

`harness` 是一个 `int main(void)`：用 `__VERIFIER_nondet_*` 产生任意输入，用 `__VERIFIER_assume` 限定写明的前提，调用接口函数，然后在下面这个固定结构里断言性质（`CHECK_REACH` 用来检查断言能否执行到，必须保留）：

```c
#ifdef CHECK_REACH
    assert(0);
#else
    assert(<性质>);
#endif
```

下面两个例子与任何具体需求无关，只说明写法。

P1 例子（假想需求"把 x 限制在 [lo, hi] 内"，前置条件 lo <= hi 写在头文件里）：

```c
/* spec */
int cv_pre_clamp(int x, int lo, int hi) { return lo <= hi; }
int cv_post_clamp(int x, int lo, int hi, int r) {
    return lo <= r && r <= hi && (x < lo ? r == lo : x > hi ? r == hi : r == x);
}
/* harness */
int main(void) {
    int x = __VERIFIER_nondet_int(), lo = __VERIFIER_nondet_int(), hi = __VERIFIER_nondet_int();
    __VERIFIER_assume(cv_pre_clamp(x, lo, hi));
    int r = clamp(x, lo, hi);
#ifdef CHECK_REACH
    assert(0);
#else
    assert(cv_post_clamp(x, lo, hi, r));
#endif
    return 0;
}
```

P2 例子（假想需求"栈中元素个数等于入栈次数减出栈次数"，任意操作序列；验证代码自己计数，不调用被测代码的计数函数）：

```c
int main(void) {
    stack_t s;
    stack_init(&s);
    unsigned expect = 0;                         /* 验证代码自己记账 */
    while (1) {
        if (__VERIFIER_nondet_bool()) {
            __VERIFIER_assume(expect < STACK_CAPACITY);   /* push 的前置条件，头文件写明 */
            stack_push(&s, __VERIFIER_nondet_int()); expect++;
        } else {
            __VERIFIER_assume(expect > 0);                /* pop 的前置条件，头文件写明 */
            (void)stack_pop(&s); expect--;
        }
#ifdef CHECK_REACH
        assert(0);
#else
        assert(s.size == expect);
#endif
    }
}
```

P4（结构约束）不写 C 代码，写目标字段和允许写它的接口函数，由静态分析检查：

```json
{"form": "property", "kind": "P4", "target": {"record": "stack_t", "field": "size"}, "allowed_writers": ["stack_init", "stack_push", "stack_pop"]}
```

## 简化参考模型的写法

`model`：定义 `cv_ref_<f>`，签名与接口函数 `f` 完全相同，用明显正确的慢办法求解；可另写 `cv_pre_<f>`。`harness`：产生输入、限定前置条件、分别调用 `f` 与 `cv_ref_<f>`，在上面的 `CHECK_REACH` 结构里断言两者相等。

## 具体用例的写法

`cases`：一个列表，每项 `{"setup": "<可选的 C 语句>", "call": "<调用接口函数的 C 表达式>", "expect": "<常量算术表达式>"}`。期望结果必须是确定的常量，直接从需求算出。

## 输出

只输出一个 JSON 代码块。C 代码放在 JSON 字符串里，换行写成 `\n`，双引号写成 `\"`：

```json
{
  "artifacts": [
    {"item": "R1", "segment": "S1", "form": "property", "kind": "P1", "reading": "<读法>", "functions": ["<函数名>"], "spec": "<C 代码>", "harness": "<C 代码>"},
    {"item": "R1", "segment": "S2", "form": "uncovered", "reason": "<原因>"}
  ]
}
```

每一段都要有至少一项（产物或 uncovered）。
