# iCode 交叉验证插件（cross-validate）

为 [openJiuwen/iCode](https://gitcode.com/openJiuwen/iCode) 实现《交叉验证与组合验证》（draft v2）第 2、3 章的**交叉验证**：
主 agent 先确定接口（C 头文件）；在实现开始之前，由只看得到需求条目与头文件的生成器从两个来源写出子任务产物，
经自检后冻结；主 agent 宣布完成时，checker 用模型检查、静态分析、差分测试或直接运行把代码与产物逐个比对，
判定器据此发出 **放行 / 阻止 / 暂停 / 拒绝** 四种信号之一。

范围只到交叉验证。组合验证（依赖—保证检查）和前提推断（第 4、5 章）不在本插件内。唯一的接触点是放行时写入的契约库
`.cv/contracts/`（算法 1 第 32 行），留给组合验证去读。

## 为什么是一个工作流

iCode 没有独立的"插件"机制，它的扩展点是工作流、agent 配置、hooks、skills 和 MCP。设计文档要求 harness 提供三项能力，
逐一对照后结论如下：

| 文档要求 | hooks | 工作流（本插件采用） |
| --- | --- | --- |
| 1 完成时的否决 | ✗ 只有 `user_prompt_submit`、`before_tool_call` 能阻断，`after_turn` 不能返回决定 | ✓ `implement` 循环：出口节点 `decide` 就是判定器；信号为"阻止"时循环把反馈交回主 agent |
| 2 计划阶段的入口 | ✗ | ✓ `plan_*` 节点位于 `interface` 之后、`implement` 之前 |
| 3 结构化的完成声明 | — | ✓ 主 agent 写 `.cv/manifests/<序号>.json` |

所以插件由两部分组成：一个 iCode **工作流文件夹** `cross-validate/`，以及生成器用的 **agent 配置** `agents/cv-generator.yaml`。
插件不使用 hooks。

## 安装

依赖（都需要在 `PATH` 上）：`clang`、`cbmc`、`goto-cc`、`goto-instrument`（CBMC 5.x），可选 `z3`（用作带冲突数上限的 SAT 求解器，
没有它时 CBMC 用内置求解器，此时没有冲突数上限）。随机测试用 `fork()`，调用日志用 GNU ld 的 `--wrap`，所以 checker 面向 Linux；
macOS 上可以运行，只是反例里没有调用序列。工作流只用 Python 标准库（iCode 不为工作流安装依赖）。

```bash
python scripts/install.py --project /path/to/your/project   # 或 --global
# 重启 iCode，让它加载 cv-generator 配置
```

`--project` 把工作流装到 `<项目>/.chrys/workflows/cross-validate/`，`--global` 装到 `~/.chrys/workflows/cross-validate/`。
两者都会把 `cv-generator.yaml` 装到 `~/.chrys/agents/`。

## 使用

输入是**需求条目集**（文档 §3 协议第 1 行）。需求处理在交叉验证的上游，插件不做拆分或改写：

```json
{
  "id": "Q-discount",
  "items": [
    {"id": "R1", "text": "下单时总价满 100 元减 20 元。", "examples": ["买 200 元的商品付 180 元"], "source": "PRD §2"}
  ],
  "unprocessed": []
}
```

```bash
icode workflow run cross-validate --trust --input .cv/requirements.json   # 或直接传 JSON；留空则读 .cv/requirements.json
```

TUI 中在 Workflows 里选 "Cross validation · 交叉验证" 即可。如果输入不是条目集而是一段文字，它会被原样当作一条条目 `R1`，报告中会注明这一点。

运行过程：

1. `interface`：主 agent（默认 `Code`）只写头文件和 `.cv/interface.json`。
2. `plan_*`：生成器划分条目、两个来源各写一份产物；判定器检查等价性、删去与示例矛盾的读法、自检（不通过重写一次）、
   查冲突、冻结到 `.cv/subtasks/`，哈希记在项目之外（`~/.chrys/cross-validate/<项目>/`）。
3. `implement` 循环：主 agent 实现并写完成清单 → `verify`（新清单？产物被改？头文件被改？）→ 头文件改了就 `replan_*` → `decide`。
4. `report`：输出报告（同时写 `.cv/report.json`）。

## 与设计文档的对应

| 文档 | 实现 |
| --- | --- |
| 判定器（plan / complete） | `cross-validate/judge.py`，函数按算法 1 的行号标注（`# 算法1 第N行`） |
| checker：selfcheck、prove、fuzz、difftest、run_cases | `cross-validate/checker/` 下同名模块；P4 静态分析在 `static_analysis.py`，读法比较与冲突在 `equivalence.py` |
| 子任务生成器的提示 | `cross-validate/prompts/`（划分、生成、重写、公共约定） |
| 生成器只读需求条目与头文件 | `cv-generator` 配置不带任何工具；它读到的只有提示本身 |
| 两个来源 | 来源 A 只读条目，来源 B 读条目加示例；`config.json` 的 `generator_a_model` / `generator_b_model` 可再换模型。两者模型相同而条目又没有示例时，报告会提示来源之间没有差异 |
| 协议的七种消息 | `cv_protocol.py` |
| 项目之外的哈希记录、重试次数、缓存 | `cv_store.py` |
| 接口函数、实现函数体与调用关系 | `cv_clang.py`（clang JSON AST） |

### 子任务产物与比对方式

| 形态 | 自检 | 比对 | 强度 |
| --- | --- | --- | --- |
| 形式化性质 P1–P3（`spec` + SV-COMP 风格 `harness`） | 能编译；在示例上成立；对"任意实现"（havoc 桩）可以不成立；`CHECK_REACH` 下 `assert(0)` 可达 | 全范围证明（带展开断言的 CBMC，或 k-induction）→ 界限内证明 → 随机测试；先确认断言可达 | `proof` / `bounded` / `fuzz` |
| 结构约束 P4（目标字段 + 允许写它的接口函数） | 字段、函数在头文件中存在，字段可写 | clang AST 静态分析：赋值、复合赋值、自增自减、取地址、整体赋值、memcpy 类调用 | `static` |
| 简化参考模型（`cv_ref_<f>` + harness） | 能编译；签名与 `f` 相同；在示例上一致 | 差分测试（随机测试引擎） | `difftest` |
| 具体用例 | 能编译；期望值是常量；与示例一致 | 直接运行（每个用例在独立子进程） | `cases` |

读法：同一来源、同一 `reading` 标签下的产物共同构成一种读法（全部通过才算通过，例如文档里"结构约束 + 序列不变量作为补充"）；
不同读法之间任一通过即通过，并记下采用的读法（文档 §3 流程 4）。

资源上限都是确定的：展开深度、k、SAT 冲突数（z3 `sat.max_conflicts`）、随机测试的迭代次数与种子。墙钟超时
（`safety_timeout_seconds`）只是防止工具卡死的安全网，触发时结果记为"未知"并写明原因。

## 配置

`cross-validate/config.json`（插件级），以及项目内 `.cv/config.json`（运行时参数可在项目内覆盖）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `main_profile` / `main_model` | `Code` / 无 | 主 agent 的配置与模型（构图时读取，只能在插件级设置） |
| `generator_profile` | `cv-generator` | 生成器配置（同上） |
| `generator_a_model` / `generator_b_model` | 无 / 无 | 两个来源的模型；建议给 B 配另一个模型（同上） |
| `max_rounds` | 12 | 工作流循环轮数的安全上限；用尽时按拒绝处理（同上） |
| `retry_limit` | 3 | 重试上限：某个函数失败达到此次数即拒绝 |
| `k_induction_max`、`unwind_full` | 2、16 | 全范围证明：k-induction 的最大 k；带展开断言的展开深度 |
| `bounded_unwind` | 6 | 有界证明的界限 |
| `sat_conflicts` | 2000000 | 求解器的冲突数上限 |
| `fuzz_iterations`、`fuzz_seed`、`fuzz_max_draws` | 1000、1、64 | 随机测试的迭代次数、随机种子、每次迭代最多取多少个值 |
| `min_strength` | `cases` | 放行所需的最低强度（`cases` < `fuzz`=`difftest` < `bounded`=`static` < `proof`） |
| `require_disambiguation` | false | 是否必须消除歧义（是则在有可区分输入时暂停） |
| `unknown_blocks` | false | "未知"是否阻止放行 |
| `cflags` | `["-std=c11"]` | 编译选项 |
| `safety_timeout_seconds` | 300 | 单次工具调用的安全超时 |

## 局限

设计文档第 3 章列出的局限，本实现同样存在：

- 需求条目集是主 agent 和生成器共用的依据。需求处理若理解错了，两边会一起错，比对中暴露不出来。
- 需求只能部分转成子任务产物时，未覆盖的部分不受检查，报告中列出。
- 用 k-induction 证明循环中的性质时，有时需要先加强不变量；证明不了时退到有界证明。
- 有多种读法时，恰好落在某种合理读法上的误读会被放过。
- 如何从需求自动推出关系性质（P3），仍是开放问题。

本实现另有的局限：

- 读法比较依赖规范谓词（`cv_pre_/cv_post_/cv_inv_/cv_rel_/cv_ref_`）。两份产物比较不了时（没有规范谓词、签名不同），按不同读法处理：
  这只可能放过误读，不会误拦正确代码（约束 2 优先于约束 1，与文档对歧义的取舍一致）。
- 冲突检查只发现"在所有输入上都不能同时成立"的前后条件或不变量，发现不了只在部分输入上矛盾的要求。
- P4 静态分析看不到经别名指针、强制类型转换的写入。
- 文档把 `.cv/subtasks/` 放在项目内，主 agent 在技术上读得到它；插件只能通过指令要求主 agent 不读，并保证反馈里从不包含产物。
  修改产物会因哈希不一致而被拒绝。
- C++ 实现：P4 静态分析可用；CBMC 对 C++ 的支持有限，形式化性质以 C 为主。
- 计划缓存的键包含 checker 版本号 `CHECKER_VERSION`。修改 checker 后需要同时提高版本号，否则会复用旧的计划。

## 标准与核验

- `STANDARDS.md`：本插件遵循的标准清单（设计文档、iCode 官方扩展规范、SV-COMP / CBMC、iCode 的 Python 代码规范、提交规范），每条带编号。
- `scripts/check_standards.py`：checker，逐条检查是否符合标准，结果写入 `CONFORMANCE.md`。

```bash
python scripts/check_standards.py --icode /path/to/iCode   # 可选：给出 iCode 源码目录以运行官方校验和端到端测试
```

## 开发

```bash
python -m pytest              # 单元与集成测试（需要 clang 与 CBMC）
uvx ruff check cross-validate tests scripts && uvx ruff format --check cross-validate tests scripts
python tests/e2e/run_icode_e2e.py /path/to/iCode   # 通过真实的 icode workflow run 跑完整流程（模型由本地脚本代替）
```

许可证：Apache-2.0（与 iCode 一致）。
