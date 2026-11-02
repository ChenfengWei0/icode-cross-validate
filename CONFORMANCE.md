# 标准核验结果

由 `python3 scripts/check_standards.py` 生成，标准见 `STANDARDS.md`。
合计：通过 133，未通过 0，跳过 0，人工 1。


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
| 细则 1.4 | 通过 | 检查一律经包装进程以 sh -c 执行（cv_run.run_command、cv_exec.py） |
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
| 细则 4.8 | 通过 | 5 个测试：test_no_alignment_answer、test_bad_numbers_in_formulas、test_bad_numbers_in_sentences、test_unreadable_propositions_are_not_crashes 等 |
| 细则 5.1 | 通过 | 2 个测试：test_verify_workflow、test_only_installed_workflow_runs |
| 细则 5.2 | 通过 | 7 个测试：test_tampering_is_detected、test_frozen_checks_run、test_tamper_fails_closed、test_results_frozen 等 |
| 细则 5.3 | 通过 | 4 个测试：test_guard_hook、test_subtask_tool_calls_invalidate、test_goal_mode_violation_log、test_violation_without_cwd_counts |
| 细则 5.4 | 通过 | 3 个测试：test_w_must_not_change、test_tamper_fails_closed、test_w_before_frozen_before_alignment |
| 细则 5.5 | 通过 | 3 个测试：test_no_process_survives、test_setsid_does_not_escape、test_planted_bytecode_is_not_read |
| 细则 5.6 | 通过 | 1 个测试：test_history_guard |
| 细则 6.1 | 通过 | 3 个测试：test_revision_paths、test_split_must_cover_r_verbatim、test_split_fallback |
| 细则 6.2 | 通过 | 1 个测试：test_labels |
| 细则 6.3 | 通过 | 2 个测试：test_goal_requirement、test_quotable |
| 细则 7.1 | 通过 | 4 个测试：test_glued_ambiguity、test_names_round_trip、test_interfaces_on_one_line、test_declarations |
| 细则 7.2 | 通过 | 4 个测试：test_checklist_in_prompt、test_checklist_ids_exist、test_interrupted_output_is_named、test_checklist |
| 细则 7.3 | 通过 | 2 个测试：test_decorated_fields_and_lost_blocks、test_propositions |
| 细则 8.1 | 通过 | 3 个测试：test_lexing、test_bad_numbers_in_formulas、test_bad_numbers_in_sentences |
| 细则 8.2 | 通过 | 1 个测试：test_terms |
| 细则 8.3 | 通过 | 2 个测试：test_atoms、test_glued_ambiguity |
| 细则 8.4 | 通过 | 1 个测试：test_connectives |
| 细则 8.5 | 通过 | 3 个测试：test_patterns、test_only_modifies_needs_a_term、test_example_may_end_with_a_call |
| 细则 8.6 | 通过 | 2 个测试：test_next_step、test_next_step_pushes_negation |
| 细则 8.7 | 通过 | 2 个测试：test_quantifiers、test_types_and_arity |
| 细则 8.8 | 通过 | 2 个测试：test_reserved_words、test_reserved_words_in_prompt |
| 细则 8.9 | 通过 | 1 个测试：test_snake_propositions |
| 细则 9.1 | 通过 | 20 个测试：test_snake_example_passes、test_fields_and_ids、test_parse_and_unpatterned、test_declared_and_bound 等 |
| 细则 9.2 | 通过 | 3 个测试：test_checklist_ids_exist、test_rewrite_checks_new_declarations、test_declarations |
| 细则 9.3 | 通过 | 4 个测试：test_revision_paths、test_rewrite_checks_new_declarations、test_rewrite_keeps_every_proposition、test_renumbered_duplicate_keeps_the_other |
| 细则 9.4 | 人工 | “一条命题只说一件事”无法机械判断，只在提示中要求（README 细则 9.4、第 18 节） |
| 细则 10.1 | 通过 | 4 个测试：test_ascii_notation、test_precedence、test_primes_anywhere、test_primed_atoms_and_formula_gaps |
| 细则 10.2 | 通过 | 3 个测试：test_round_trip、test_ops、test_names_round_trip |
| 细则 10.3 | 通过 | 5 个测试：test_weak_next、test_nnf_duals、test_no_contradiction_is_true、test_primes_anywhere 等 |
| 细则 10.4 | 通过 | 1 个测试：test_classes |
| 细则 11.1 | 通过 | 1 个测试：test_align_input |
| 细则 11.2 | 通过 | 2 个测试：test_validate_format、test_no_links_in_align |
| 细则 11.3 | 通过 | 1 个测试：test_command_checks |
| 细则 11.4 | 通过 | 1 个测试：test_trace_checks |
| 细则 11.5 | 通过 | 1 个测试：test_coverage |
| 细则 11.6 | 通过 | 2 个测试：test_coverage、test_no_alignment_answer |
| 细则 11.7 | 通过 | 5 个测试：test_validate_format、test_revision_paths、test_preflight_reports_the_format_and_nothing_else、test_preflight_goes_back_once 等 |
| 细则 11.8 | 通过 | 4 个测试：test_budget、test_truncation、test_budget_cut_does_not_pass、test_judging_is_bounded |
| 细则 11.9 | 通过 | 1 个测试：test_frozen_checks_run |
| 细则 11.10 | 通过 | 1 个测试：test_alignment_prompt_asks_for_strict_checks_that_reach_the_hard_cases |
| 细则 12.1 | 通过 | 1 个测试：test_trace_format |
| 细则 12.2 | 通过 | 1 个测试：test_time_convention |
| 细则 12.3 | 通过 | 5 个测试：test_values、test_no_contradiction_is_true、test_big_integers_exact、test_missing_key_after_violation 等 |
| 细则 12.4 | 通过 | 4 个测试：test_entity_constant、test_listed_entities、test_only_individual_may_be_keyed_by_entity_name、test_entity_with_no_individual_now_is_false_not_an_error |
| 细则 12.5 | 通过 | 1 个测试：test_scenario |
| 细则 12.6 | 通过 | 6 个测试：test_nonzero_exit_fails、test_empty_trace、test_trace_must_be_a_regular_file、test_partial_line_after_timeout 等 |
| 细则 13.1 | 通过 | 2 个测试：test_four_values、test_progression_matches_reference |
| 细则 13.2 | 通过 | 3 个测试：test_four_values、test_facts_at_state_zero、test_progression_matches_reference |
| 细则 13.3 | 通过 | 4 个测试：test_trace_checks、test_order、test_missing_key_after_violation、test_coverage_failure_does_not_hide_a_failure |
| 细则 13.4 | 通过 | 2 个测试：test_trace_checks、test_crash_counterexample_kept |
| 细则 13.5 | 通过 | 2 个测试：test_liveness、test_liveness_mark |
| 细则 13.6 | 通过 | 1 个测试：test_trace_checks |
| 细则 13.7 | 通过 | 1 个测试：test_antecedent_never_fired |
| 细则 14.1 | 通过 | 2 个测试：test_conclusions、test_report_text_cannot_forge_conclusion |
| 细则 14.2 | 通过 | 2 个测试：test_tampering_is_detected、test_conclusions |
| 细则 14.3 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 15.1 | 通过 | 3 个测试：test_init_rules、test_ignored_project_is_not_git、test_hooks_find_enabled_parent |
| 细则 15.2 | 通过 | 2 个测试：test_record_hook_filters、test_append_ids_and_lock |
| 细则 15.3 | 通过 | 2 个测试：test_preprocess、test_consolidation_input_skips_dropped |
| 细则 15.4 | 通过 | 4 个测试：test_accumulation_mode、test_consolidation_ok、test_consolidation_errors、test_consolidation_input_skips_dropped |
| 细则 15.5 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 15.6 | 通过 | 1 个测试：test_new_ledger |
| 细则 16.1 | 通过 | 1 个测试：test_turn_and_session_triggers |
| 细则 16.2 | 通过 | 2 个测试：test_turn_and_session_triggers、test_unreadable_file_stops_with_report |
| 细则 16.3 | 通过 | 2 个测试：test_lock_and_pending、test_pending_after_release |
| 细则 16.4 | 通过 | 3 个测试：test_snapshot、test_unreadable_file_stops_with_report、test_directory_links_in_snapshot |
| 细则 16.5 | 通过 | 1 个测试：test_record_hook_filters |
| 细则 16.6 | 通过 | 3 个测试：test_record_hook_filters、test_check_prints_no_report、test_report_text_cannot_forge_conclusion |
| 细则 16.7 | 通过 | 4 个测试：test_install_uninstall、test_hooks_yaml_round_trip、test_install_keeps_user_hooks、test_install_refuses_before_changing |
| 细则 17.1 | 通过 | 偏离说明，核验其实现细则 6.1 |
| 细则 17.2 | 通过 | 偏离说明，核验其实现细则 8.9 |
| 细则 17.3 | 通过 | 偏离说明，核验其实现细则 10.3 |
| 细则 17.4 | 通过 | 偏离说明，核验其实现细则 11.5 |
| 细则 17.5 | 通过 | 偏离说明，核验其实现细则 16.1、16.6 |
| 细则 17.6 | 通过 | 偏离说明，核验其实现细则 15.4、6.3 |
| 细则 17.7 | 通过 | 偏离说明，核验其实现细则 7.1 |
| 细则 17.8 | 通过 | 偏离说明，核验其实现细则 14.2 |
| 细则 17.9 | 通过 | 1 个测试：test_translation_of_a_rule_and_an_example |
| 细则 17.10 | 通过 | 1 个测试：test_state_dependent_needs_a_time |

## I：iCode 扩展规范

| 编号 | 结果 | 依据 |
| --- | --- | --- |
| I-AG-1 | 通过 | 两个配置都有 name、display_name、description、instructions，sub_agent_only: true |
| I-AG-2 | 通过 | cv-subtask：builtins 为空，skills 不自动加载，没有 memory |
| I-AG-3 | 通过 | cv-align builtins：['filesystem.read', 'filesystem.write', 'search', 'shell'] |
| I-HK-1 | 通过 | 7 个 hook：cv-record、cv-turn、cv-session-end、cv-subtask-guard、cv-history-guard-path、cv-history-guard-command、cv-history-guard-workdir |
| I-HK-2 | 通过 | cv_cli.hook 从 CHRYS_HOOK_PAYLOAD_FILE 读输入，向 CHRYS_HOOK_RESULT 写决定 |
| I-HK-3 | 通过 | PASS 1 file · 7 hooks · 1 warning |
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
| P-3 | 通过 | ruff check：All checks passed!；ruff format：53 files already formatted |
| P-4 | 通过 | 插件本体 252 KB；含 iCode 版权声明的文件：无 |
| P-5 | 通过 | pytest：213 passed in 119.01s (0:01:59) |
| P-6 | 通过 | 重建以来 12 个提交 |
| P-7 | 通过 | 含令牌或密钥的文件：无 |
