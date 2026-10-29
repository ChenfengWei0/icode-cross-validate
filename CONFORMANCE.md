# 标准核验结果

由 `python3 scripts/check_standards.py` 生成，标准见 `STANDARDS.md`。
合计：通过 126，未通过 0，跳过 0，人工 1。


## D：文档要求

| 编号 | 结果 | 依据 |
| --- | --- | --- |
| D-1 | 通过 | 只做交叉验证，不做组合验证：细则 1.1 |
| D-2 | 通过 | harness 报告目标达成时介入，是收尾检查：细则 1.2、4.2、16.1；1 个测试：test_goal_mode_snake |
| D-3 | 通过 | 输入是最终需求原文和代码工作区：细则 4.1、15.4、17.6 |
| D-4 | 通过 | 子任务只看需求，看不到主任务的任何产物：细则 4.6、5.3、11.1、15.3 |
| D-5 | 通过 | 子任务 agent 把 R 切成单句并分类：细则 6.1、6.2 |
| D-6 | 通过 | 带标签的句子按关键词选句式翻译，依据引用原句，示例不推广，接口照原文：细则 6.3、7.3、9.1；1 个测试：test_example_two_passes |
| D-7 | 通过 | 按九项清单逐项推理，写明理由，不读 W：细则 7.2、9.1；1 个测试：test_checklist_in_prompt |
| D-8 | 通过 | 只用已声明的实体、谓词和数量；套用句式或注明未套用；不含实现上的名字：细则 9.1、9.2；1 个测试：test_snake_example_passes |
| D-9 | 通过 | 句子到公式由程序机械生成，每个句式唯一对应一个公式：细则 ；1 个测试：test_snake_propositions |
| D-10 | 通过 | 命题有编号、来源、依据、句子、公式五个字段：细则 7.3 |
| D-11 | 通过 | 实体、谓词、数量先声明，附含义；谓词带上对象：细则 7.1 |
| D-12 | 通过 | 对齐 agent 读 W，自行决定绑定和检查形态：细则 11.1、11.2；1 个测试：test_align_input |
| D-13 | 通过 | 对齐失败记为失败，算交叉验证失败：细则 11.6、13.3 |
| D-14 | 通过 | 事实与结构命题得通过或失败；时序命题得四值之一，多次运行取最差：细则 11.3、11.4 |
| D-15 | 通过 | 四值的含义（RV-LTL）：细则 13.1、13.2；1 个测试：test_four_values |
| D-16 | 通过 | 只要求坏事不发生的命题，没发现违反时得到推定为真：细则 10.3、17.3；1 个测试：test_weak_next |
| D-17 | 通过 | 报告列出需求、代码版本、每条命题的来源、结果和反例，以及翻译、推理、未套用句式的数量：细则 14.1；1 个测试：test_conclusions |
| D-18 | 通过 | 报告是概率凭证，满足式 (7) 时才高于基准正确率：细则 14.1 |
| D-19 | 通过 | 子任务只陈述性质，不重写实现，比主任务简单：细则 ；1 个测试：test_example_one_passes |
| D-20 | 通过 | 子任务可以换一个模型：细则 2.2 |
| D-21 | 通过 | 实现算法 1 每一行的代码有标注：细则 4.5；1 个测试：test_algorithm_lines_annotated |
| D-22 | 通过 | R1 至 R10 能按文法写出，公式与文档一致：细则 8.9、17.2；2 个测试：test_snake_propositions、test_goal_mode_snake |

## R：README 细则

| 编号 | 结果 | 依据 |
| --- | --- | --- |
| 细则 1.1 | 通过 | 没有组合验证的模块 |
| 细则 1.2 | 通过 | 工作流没有回到主 agent 的循环，也不向主 agent 追加指令 |
| 细则 1.3 | 通过 | 需求积累在 cv_ledger.py 与 cv_cli.py |
| 细则 1.4 | 通过 | 检查一律以 sh -c 执行（cv_run.run_command） |
| 细则 2.1 | 通过 | 1 个测试：test_install_uninstall；工作流、两个 agent 配置、hooks、cv 的六个子命令齐全 |
| 细则 2.2 | 通过 | config.json 有 main_model, subtask_model, align_model |
| 细则 3.1 | 通过 | 1 个测试：test_state_outside_project |
| 细则 3.2 | 通过 | 1 个测试：test_state_outside_project |
| 细则 4.1 | 通过 | 2 个测试：test_intake_modes、test_accumulation_mode |
| 细则 4.2 | 通过 | 2 个测试：test_goal_requirement、test_goal_mode_snake |
| 细则 4.3 | 通过 | 1 个测试：test_accumulation_mode |
| 细则 4.4 | 通过 | 2 个测试：test_graph_builds、test_goal_mode_snake |
| 细则 4.5 | 通过 | 1 个测试：test_algorithm_lines_annotated |
| 细则 4.6 | 通过 | 1 个测试：test_placeholders_filled |
| 细则 4.7 | 通过 | agent 节点用 config 的超时，run_checks 无超时，不设自动重试 |
| 细则 5.1 | 通过 | 2 个测试：test_verify_workflow、test_only_installed_workflow_runs |
| 细则 5.2 | 通过 | 1 个测试：test_tampering_is_detected |
| 细则 5.3 | 通过 | 3 个测试：test_guard_hook、test_subtask_tool_calls_invalidate、test_goal_mode_violation_log |
| 细则 5.4 | 通过 | 1 个测试：test_w_must_not_change |
| 细则 6.1 | 通过 | 3 个测试：test_revision_paths、test_split_must_cover_r_verbatim、test_split_fallback |
| 细则 6.2 | 通过 | 1 个测试：test_labels |
| 细则 6.3 | 通过 | 2 个测试：test_goal_requirement、test_quotable |
| 细则 7.1 | 通过 | 1 个测试：test_declarations |
| 细则 7.2 | 通过 | 2 个测试：test_checklist_in_prompt、test_checklist |
| 细则 7.3 | 通过 | 1 个测试：test_propositions |
| 细则 8.1 | 通过 | 1 个测试：test_lexing |
| 细则 8.2 | 通过 | 1 个测试：test_terms |
| 细则 8.3 | 通过 | 1 个测试：test_atoms |
| 细则 8.4 | 通过 | 1 个测试：test_connectives |
| 细则 8.5 | 通过 | 1 个测试：test_patterns |
| 细则 8.6 | 通过 | 1 个测试：test_next_step |
| 细则 8.7 | 通过 | 1 个测试：test_quantifiers |
| 细则 8.8 | 通过 | 2 个测试：test_reserved_words、test_reserved_words_in_prompt |
| 细则 8.9 | 通过 | 1 个测试：test_snake_propositions |
| 细则 9.1 | 通过 | 9 个测试：test_snake_example_passes、test_fields_and_ids、test_parse_and_unpatterned、test_declared_and_bound 等 |
| 细则 9.2 | 通过 | 1 个测试：test_declarations |
| 细则 9.3 | 通过 | 1 个测试：test_revision_paths |
| 细则 9.4 | 人工 | “一条命题只说一件事”无法机械判断，只在提示中要求（README 细则 9.4、第 18 节） |
| 细则 10.1 | 通过 | 2 个测试：test_ascii_notation、test_precedence |
| 细则 10.2 | 通过 | 2 个测试：test_round_trip、test_ops |
| 细则 10.3 | 通过 | 2 个测试：test_weak_next、test_nnf_duals |
| 细则 10.4 | 通过 | 1 个测试：test_classes |
| 细则 11.1 | 通过 | 1 个测试：test_align_input |
| 细则 11.2 | 通过 | 1 个测试：test_validate_format |
| 细则 11.3 | 通过 | 1 个测试：test_command_checks |
| 细则 11.4 | 通过 | 1 个测试：test_trace_checks |
| 细则 11.5 | 通过 | 1 个测试：test_coverage |
| 细则 11.6 | 通过 | 1 个测试：test_coverage |
| 细则 11.7 | 通过 | 2 个测试：test_validate_format、test_revision_paths |
| 细则 11.8 | 通过 | 2 个测试：test_budget、test_truncation |
| 细则 11.9 | 通过 | 1 个测试：test_frozen_checks_run |
| 细则 12.1 | 通过 | 1 个测试：test_trace_format |
| 细则 12.2 | 通过 | 1 个测试：test_time_convention |
| 细则 12.3 | 通过 | 1 个测试：test_values |
| 细则 12.4 | 通过 | 1 个测试：test_entity_constant |
| 细则 12.5 | 通过 | 1 个测试：test_scenario |
| 细则 12.6 | 通过 | 2 个测试：test_nonzero_exit_fails、test_empty_trace |
| 细则 13.1 | 通过 | 1 个测试：test_four_values |
| 细则 13.2 | 通过 | 2 个测试：test_four_values、test_facts_at_state_zero |
| 细则 13.3 | 通过 | 2 个测试：test_trace_checks、test_order |
| 细则 13.4 | 通过 | 1 个测试：test_trace_checks |
| 细则 13.5 | 通过 | 1 个测试：test_liveness |
| 细则 13.6 | 通过 | 1 个测试：test_trace_checks |
| 细则 14.1 | 通过 | 1 个测试：test_conclusions |
| 细则 14.2 | 通过 | 2 个测试：test_tampering_is_detected、test_conclusions |
| 细则 14.3 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 15.1 | 通过 | 1 个测试：test_init_rules |
| 细则 15.2 | 通过 | 2 个测试：test_record_hook_filters、test_append_ids_and_lock |
| 细则 15.3 | 通过 | 2 个测试：test_preprocess、test_consolidation_input_skips_dropped |
| 细则 15.4 | 通过 | 4 个测试：test_accumulation_mode、test_consolidation_ok、test_consolidation_errors、test_consolidation_input_skips_dropped |
| 细则 15.5 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 15.6 | 通过 | 1 个测试：test_new_ledger |
| 细则 16.1 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 16.2 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 16.3 | 通过 | 1 个测试：test_lock_and_pending |
| 细则 16.4 | 通过 | 1 个测试：test_snapshot |
| 细则 16.5 | 通过 | 1 个测试：test_record_hook_filters |
| 细则 16.6 | 通过 | 1 个测试：test_record_hook_filters |
| 细则 16.7 | 通过 | 1 个测试：test_install_uninstall |
| 细则 17.1 | 通过 | 偏离说明，核验其实现细则 6.1 |
| 细则 17.2 | 通过 | 偏离说明，核验其实现细则 8.9 |
| 细则 17.3 | 通过 | 偏离说明，核验其实现细则 10.3 |
| 细则 17.4 | 通过 | 偏离说明，核验其实现细则 11.5 |
| 细则 17.5 | 通过 | 偏离说明，核验其实现细则 16.1、16.6 |
| 细则 17.6 | 通过 | 偏离说明，核验其实现细则 15.4、6.3 |
| 细则 17.7 | 通过 | 偏离说明，核验其实现细则 7.1 |
| 细则 17.8 | 通过 | 偏离说明，核验其实现细则 14.2 |

## I：iCode 扩展规范

| 编号 | 结果 | 依据 |
| --- | --- | --- |
| I-AG-1 | 通过 | 两个配置都有 name、display_name、description、instructions，sub_agent_only: true |
| I-AG-2 | 通过 | cv-subtask：builtins 为空，skills 不自动加载，没有 memory |
| I-AG-3 | 通过 | cv-align builtins：['filesystem.read', 'filesystem.write', 'search', 'shell'] |
| I-HK-1 | 通过 | 4 个 hook：cv-record、cv-turn、cv-session-end、cv-subtask-guard |
| I-HK-2 | 通过 | cv_cli.hook 从 CHRYS_HOOK_PAYLOAD_FILE 读输入，向 CHRYS_HOOK_RESULT 写决定 |
| I-HK-3 | 通过 | PASS 1 file · 4 hooks · 1 warning |
| I-WF-1 | 通过 | cross-validate/cross-validate.py 顶层只有导入、函数定义、赋值和构图调用，并定义 workflow |
| I-WF-2 | 通过 | 与标准库同名：无；相对导入或第三方导入：无 |
| I-WF-3 | 通过 | 1 个测试：test_graph_builds |
| I-WF-4 | 通过 | 1 个测试：test_goal_mode_snake |
| I-WF-5 | 通过 | icode workflow validate：fail；bindings 之外的错误 0 个 |

## P：工程规范

| 编号 | 结果 | 依据 |
| --- | --- | --- |
| P-1 | 通过 | 只导入标准库 |
| P-2 | 通过 | ruff target-version = py310，workflow 声明 requires-python >=3.10 |
| P-3 | 通过 | ruff check：All checks passed!；ruff format：46 files already formatted |
| P-4 | 通过 | 插件本体 190 KB；含 iCode 版权声明的文件：无 |
| P-5 | 通过 | pytest：123 passed in 27.25s |
| P-6 | 通过 | 重建以来 3 个提交 |
| P-7 | 通过 | 含令牌或密钥的文件：无 |
