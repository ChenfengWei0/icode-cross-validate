# 标准符合性报告（CONFORMANCE）

由 `scripts/check_standards.py` 生成，逐条对应 `STANDARDS.md`。

- 结果：59 PASS · 0 FAIL · 0 SKIP（共 59 条）
- iCode 校验：基于 iCode（108a9b2）
- 未被检查的标准：无；清单外的检查：无

| 编号 | 结果 | 检查方式 | 证据 |
| --- | --- | --- | --- |
| D-SCOPE-1 | PASS | static: no composition/premise-inference definitions; the contract store is only written (judge._write_contracts) | no rely/guarantee/premise/abduction code; `.cv/contracts` written only by judge.py, never read |
| D-SCOPE-2 | PASS | pytest | `test_protocol::test_free_text_is_passed_through_untouched` |
| D-SCOPE-3 | PASS | static + test | validate_interface accepts only headers; C++ delivery rejected: `test_regressions::test_cpp_delivery_is_rejected` |
| D-COMP-1 | PASS | profile has no tools; prompt builders read no implementation; test with an implementation present | cv-generator: tools.builtins=[] and no MCP; segment_prompt, generation_prompt, rewrite_prompt read only items/headers; `test_visibility::test_generator_prompts_never_contain_the_implementation` |
| D-COMP-2 | PASS | pytest | `test_visibility::test_source_a_reads_the_item_only_and_source_b_adds_the_examples`, `test_visibility::test_same_model_and_no_examples_is_reported`, `test_visibility::test_prompt_examples_do_not_suggest_a_reading_of_the_design_examples` |
| D-COMP-3 | PASS | static: checker/ imports no network or model client | checker/ imports only stdlib and local modules |
| D-COMP-4 | PASS | static: the judge imports no network or model client | judge.py, cv_*.py: no model calls |
| D-COMP-5 | PASS | static: graph order interface → plan → implement | edges interface→plan_entry, plan_exit→implement |
| D-HARN-1 | PASS | graph: loop `implement` exits at the judge; end-to-end run: block then pass | loop until=settled; `test_icode_e2e::test_block_then_pass_through_icode` |
| D-HARN-2 | PASS | static: graph order interface → plan → implement | edges interface→plan_entry, plan_exit→implement |
| D-HARN-3 | PASS | pytest | `test_discount::test_invalid_declaration_is_sent_back`, `test_protocol::test_manifest_must_name_the_item_set_and_files` |
| D-PROT-1 | PASS | pytest | `test_protocol::test_item_set_fields_and_default_id`, `test_protocol::test_empty_item_set_passes_with_a_note` |
| D-PROT-2 | PASS | pytest | `test_protocol::test_missing_interface_leaves_everything_uncovered`, `test_discount::test_header_change_replans_the_affected_items` |
| D-PROT-3 | PASS | pytest | `test_visibility::test_generator_prompts_never_contain_the_implementation` |
| D-PROT-4 | PASS | pytest | `test_visibility::test_frozen_hashes_are_recorded_outside_the_project`, `test_discount::test_tampered_artifact_is_rejected` |
| D-PROT-5 | PASS | pytest | `test_discount::test_no_new_declaration_passes_without_checking`, `test_discount::test_invalid_declaration_is_sent_back` |
| D-PROT-6 | PASS | pytest | `test_discount::test_results_are_deterministic`, `test_selfcheck::test_good_property_passes_all_four_checks` |
| D-PROT-7 | PASS | pytest | `test_discount::test_code_matching_no_reading_is_blocked_with_counterexample_only`, `test_discount::test_report_tells_first_pass_from_pass_after_modification`, `test_discount::test_first_pass_is_reported_as_such`, `test_forms::test_concrete_cases_run_directly` |
| D-SIG-1 | PASS | pytest | `test_discount::test_any_reading_passing_passes_and_records_the_reading`, `test_discount::test_code_matching_no_reading_is_blocked_with_counterexample_only`, `test_discount::test_pause_when_disambiguation_is_required`, `test_discount::test_retry_limit_rejects` |
| D-SM-1 | PASS | pytest | `test_selfcheck::test_compile_error_is_rewritten_once_and_kept`, `test_selfcheck::test_rewrite_that_still_fails_is_dropped` |
| D-SM-2 | PASS | pytest | `test_protocol::test_empty_item_set_passes_with_a_note`, `test_discount::test_tampered_artifact_is_rejected`, `test_discount::test_header_change_replans_the_affected_items`, `test_discount::test_retry_limit_rejects` |
| D-FLOW-1 | PASS | pytest | `test_protocol::test_empty_item_set_passes_with_a_note`, `test_discount::test_no_new_declaration_passes_without_checking` |
| D-FLOW-2 | PASS | pytest | `test_selfcheck::test_segment_span_must_come_from_the_item`, `test_selfcheck::test_uncovered_segment_keeps_the_generators_reason`, `test_regressions::test_segment_without_functions_is_uncovered`, `test_forms::test_reference_model_by_differential_testing`, `test_forms::test_concrete_cases_run_directly` |
| D-FLOW-3 | PASS | pytest | `test_ledger::test_plan_freezes_structure_and_invariant`, `test_visibility::test_source_a_reads_the_item_only_and_source_b_adds_the_examples` |
| D-FLOW-4 | PASS | pytest | `test_discount::test_two_readings_are_kept_with_a_distinguishing_input`, `test_discount::test_example_removes_the_contradicting_reading`, `test_discount::test_any_reading_passing_passes_and_records_the_reading` |
| D-FLOW-5 | PASS | pytest | `test_selfcheck::test_good_property_passes_all_four_checks`, `test_selfcheck::test_vacuous_property_is_discarded`, `test_selfcheck::test_contradictory_premises_are_discarded`, `test_selfcheck::test_missing_reach_branch_is_a_failure`, `test_regressions::test_harness_must_assert_the_spec_it_defines`, `test_regressions::test_inconclusive_self_check_is_not_a_pass`, `test_forms::test_cases_need_a_constant_expectation`, `test_forms::test_contradicting_requirements_are_marked_as_conflict`, `test_regressions::test_equal_predicates_under_different_premises_stay_two_readings` |
| D-FLOW-6 | PASS | pytest | `test_discount::test_tampered_artifact_is_rejected`, `test_ledger::test_correct_implementation_passes_with_proof`, `test_ledger::test_bypassing_the_payment_module_is_blocked`, `test_forms::test_random_testing_finds_the_counterexample`, `test_forms::test_reference_model_by_differential_testing`, `test_forms::test_concrete_cases_run_directly`, `test_discount::test_results_are_deterministic`, `test_regressions::test_code_that_does_not_compile_is_blocked`, `test_regressions::test_code_that_never_returns_is_blocked`, `test_regressions::test_undefined_function_is_blocked`, `test_regressions::test_wr |
| D-FLOW-7 | PASS | pytest | `test_discount::test_code_matching_no_reading_is_blocked_with_counterexample_only`, `test_discount::test_retry_limit_rejects`, `test_discount::test_pause_when_disambiguation_is_required`, `test_forms::test_min_strength_turns_weak_passes_into_unknown`, `test_forms::test_unknown_blocks_when_configured`, `test_selfcheck::test_rewrite_that_still_fails_is_dropped`, `test_visibility::test_feedback_and_contracts_on_pass` |
| D-ALG-1 | PASS | static: every line 1-33 of 算法 1 is annotated in the code (ranges of at most 4 lines) | lines 1-33 all annotated |
| D-INT-1 | PASS | static: plugin layout | cross-validate/{judge, checker/{selfcheck,prove,fuzz,difftest,run_cases}, prompts/} |
| D-INT-2 | PASS | static + test | .cv/{subtasks,manifests,contracts} created by intake; `test_visibility::test_frozen_hashes_are_recorded_outside_the_project` |
| D-INT-3 | PASS | static: cache keys | plan key = item text+examples, headers, types, generator+prompt version; compare key = artifact + every delivered file (superset of checked bodies and callees) + headers, checker version, limits; `test_regressions::test_changed_callee_is_not_served_from_cache` |
| D-INT-4 | PASS | static: config.json keys | retry_limit, bounded_unwind, sat_conflicts, fuzz_iterations, fuzz_seed, min_strength, require_disambiguation, unknown_blocks |
| D-LIM-1 | PASS | static: README lists the design's limitations | all five limitations of §3 局限 |
| I-WF-1 | PASS | static: entry file named after the folder; module-level `workflow = wf.build()`; top level only builds the graph | cross-validate/cross-validate.py; `workflow: Workflow = wf.build()` |
| I-WF-2 | PASS | static: names vs sys.stdlib_module_names; relative imports; symlinks; file count | 31 files, no stdlib names, no relative imports, no links |
| I-WF-3 | PASS | static: script metadata block | requires-python declared |
| I-WF-4 | PASS | icode workflow validate: load stage (SDK checks callables) | stages={'resolve': 'pass', 'read': 'pass', 'metadata': 'pass', 'environment': 'pass', 'load': 'pass', 'graph': 'pass', 'bindings': 'pass'} |
| I-WF-5 | PASS | static: every node in front of an agent returns `str` | interface_prompt, prompt_a, prompt_b, reprompt_a, reprompt_b, round_prompt, segment_prompt |
| I-WF-6 | PASS | icode workflow validate: graph stage, no loop warnings | diagnostics=[] |
| I-WF-7 | PASS | static: no ctx.ask | the workflow never asks; a pause ends the run with the distinguishing input |
| I-WF-8 | PASS | static: checker-running python nodes set timeout; no Retry on python nodes | check/freeze/prepare/verify/decide: timeout=None (each tool call has its own safety timeout) |
| I-WF-9 | PASS | icode workflow validate --json | PASS · 46 nodes · 56 edges · 0 diagnostics |
| I-AG-1 | PASS | static: profile fields | name=cv-generator; no id; display_name, description, instructions present |
| I-AG-2 | PASS | static: generator profile has no built-in tools, no MCP, no skill scripts | tools.builtins=[], skills.script_extensions=[], auto_load_* false |
| I-AG-3 | PASS | icode agent list --json (isolated HOME) | cv-generator loaded from ~/.chrys/agents |
| I-HK-1 | PASS | static: no hook files; README explains why | README §为什么是一个工作流 |
| V-SV-1 | PASS | static: prompt conventions, checker prelude and random-testing runtime use the SV-COMP interface | __VERIFIER_nondet_<type>, __VERIFIER_assume, assert |
| V-CBMC-1 | PASS | static + tests | unwinding assertions and k-induction base/step cases; `test_ledger::test_correct_implementation_passes_with_proof`, `test_discount::test_any_reading_passing_passes_and_records_the_reading` |
| V-DET-1 | PASS | static + test | z3 sat.max_conflicts; seeded random testing; `test_discount::test_results_are_deterministic` |
| P-PY-1 | PASS | ruff check + ruff format --check with iCode's rule set | ruff: All checks passed!; format clean; rule set equal to iCode's: True |
| P-PY-2 | PASS | static: imports of the plugin | standard library and local modules only (plus chrys.workflows, supplied by iCode) |
| P-PY-3 | PASS | pytest | 62 passed, 0 skipped, 0 failed; ledger and discount examples covered |
| G-GIT-1 | PASS | git log subjects vs Conventional Commits 1.0.0 | 5 commits |
| G-LIC-1 | PASS | static: LICENSE and pyproject | Apache-2.0 |
| X-DEV-1 | PASS | README 局限 + test | stated in README 局限; `test_regressions::test_cpp_delivery_is_rejected` |
| X-DEV-2 | PASS | README 局限 + test | stated in README 局限; `test_regressions::test_stopping_without_a_declaration_is_blocked_in_the_workflow` |
| X-DEV-3 | PASS | README 局限 + test | stated in README 局限; `test_regressions::test_interface_declared_late_is_planned_before_comparison` |
| X-DEV-4 | PASS | README 局限 + test | stated in README 局限; `test_discount::test_tampered_artifact_is_rejected` |
