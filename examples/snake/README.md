# 示例：贪吃蛇（文档第 3 章）

文档第 3 章用 `/goal 实现贪吃蛇。` 作为例子。本目录用它测试插件。

| 文件 | 内容 |
| --- | --- |
| `correct/snake.py`、`buggy/snake.py` | 主任务的代码；`buggy` 中蛇撞到自身后游戏不结束 |
| `split.json`、`subtask.txt` | 子任务 agent 的回答：切句，以及文档的声明、清单和 R1–R10（R6、R9 按细则 17.2 改写） |
| `align/` | 对齐 agent 的回答：`checks.json`、插桩加模糊测试脚本 `run_snake.py`、结构检查 `r9_structure.py` |
| `run_example.py` | 不经过 iCode，依次调用工作流各节点的函数 |
| `e2e/mock_model.py` | 一个回放上述回答的本地模型服务，让真实的 iCode 在没有 API key 时跑完整个插件 |

## 不经过 iCode

```
python3 examples/snake/run_example.py correct
python3 examples/snake/run_example.py buggy
```

## 经过真实的 iCode

在一个临时的 HOME 中安装插件，配置指向 `mock_model.py` 的模型，然后：

```
python3 examples/snake/e2e/mock_model.py 18777 buggy &      # 或 correct
icode workflow run cross-validate --trust --input "实现贪吃蛇。"   # 目标模式

cv init . && icode run "实现贪吃蛇。" --agent Code            # 积累模式：hooks 记录消息，会话结束时触发
cv status .
```

模型配置是 `~/.chrys/models/<id>.yaml`：`provider: openai`、`api_style: chat_completions`、`base_url: http://127.0.0.1:18777/v1`、`stream: false`，并在 `settings.yaml` 中设 `model.profile.active`。设置 `MOCK_SUBTASK_TOOL=1` 时，子任务 agent 会先尝试调用一次工具，用来检查拦截 hook。

## 结果（2026-10-30，iCode 0.32.2）

| 运行 | 结论 | 说明 |
| --- | --- | --- |
| `correct`，目标模式和积累模式 | 比对通过 | R1、R9 通过；R2–R7、R10 推定为真；R8 推定为假（活性命题） |
| `buggy`，目标模式 | 比对未通过 | R5 确定违反，与文档示例报告一致；R2、R10 也确定违反，因为蛇身重叠后头尾重合、长度超过格子数 |
| 子任务 agent 调用工具 | 无效 | hook 拦下调用并记录，报告标为无效（细则 5.3） |

测试中发现文档示例的两处问题，插件按正文语义如实判定，没有为它们特殊处理：

- **R8** `G(¬结束(游戏) → F 移动(蛇))`：游戏因撞到自身结束时，最后一步蛇头没有移动，而那一刻游戏还没结束，所以正确的实现在有限轨迹上得到推定为假，不是文档示例报告中的推定为真。按无限轨迹语义它本来就不成立；更合适的写法是“只要 游戏 没有结束，最终 蛇 移动 或者 游戏 结束”。
- **R6** `G(新生成(食物) → ¬位于蛇身(食物))`：按细则 12.2 的时间约定，状态 i 的事件是 i 到 i+1 这一步，而“位于蛇身”是时刻 i 的状态，所以 R6 检查的是被吃掉的旧食物，几乎总是成立。要检查新食物，应写成“只要 食物 新生成，下一步 食物 不 位于蛇身”。
