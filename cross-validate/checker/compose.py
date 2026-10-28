"""The C files the checker builds from an artifact.

An artifact stores only what the generator wrote (spec, harness, model, cases). The prelude (the
headers and the SV-COMP declarations) and the driver code are added here, at check time, so they
are the same for every artifact and never depend on the generator.

Canonical predicate names, used to compare readings and to check examples and conflicts:

* ``cv_pre_<f>(params of f)``           the documented precondition of interface function f
* ``cv_post_<f>(params of f, result)``  P1: what a call of f must satisfy
* ``cv_inv_<T>(const T *s)``            P2: a state invariant over record type T
* ``cv_rel_<name>(...)``                P3: a relation between calls
* ``cv_ref_<f>(params of f)``           reference model of f, same signature as f
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from checker.common import Context, prelude, write

CANONICAL_PREFIXES = ("cv_pre_", "cv_post_", "cv_inv_", "cv_rel_", "cv_ref_")


def property_file(ctx: Context, artifact: dict[str, Any]) -> Path:
    c = artifact["content"]
    text = prelude(ctx) + "\n/* spec */\n" + c.get("spec", "") + "\n/* harness */\n" + c["harness"] + "\n"
    return write(ctx, f"{artifact['id']}.c", text)


def reference_file(ctx: Context, artifact: dict[str, Any]) -> Path:
    c = artifact["content"]
    text = prelude(ctx) + "\n/* model */\n" + c["model"] + "\n/* harness */\n" + c["harness"] + "\n"
    return write(ctx, f"{artifact['id']}.c", text)


def spec_text(artifact: dict[str, Any]) -> str:
    """The part of an artifact that defines canonical predicates or models."""
    c = artifact["content"]
    return c.get("spec", "") if artifact["form"] == "property" else c.get("model", "")


def spec_file(ctx: Context, artifact: dict[str, Any], main: str, *, tag: str) -> Path:
    text = prelude(ctx) + "\n" + spec_text(artifact) + "\n" + main + "\n"
    return write(ctx, f"{artifact['id']}.{tag}.c", text)


CASES_RUNTIME = r"""
#include <stdio.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <unistd.h>
static void cv_show_ll(long long v) { printf("%lld", v); }
static void cv_show_ull(unsigned long long v) { printf("%llu", v); }
static void cv_show_d(double v) { printf("%.17g", v); }
static void cv_show_ld(long double v) { printf("%.21Lg", v); }
#define CV_SHOW(x) _Generic((x), float: cv_show_d, double: cv_show_d, long double: cv_show_ld, \
    unsigned char: cv_show_ull, unsigned short: cv_show_ull, unsigned int: cv_show_ull, \
    unsigned long: cv_show_ull, unsigned long long: cv_show_ull, default: cv_show_ll)(x)
"""


def cases_program(ctx: Context, artifact: dict[str, Any]) -> Path:
    """One function per case; ``main`` runs each in a forked child and prints one JSON line per case."""
    cases = artifact["content"]["cases"]
    parts = [prelude(ctx), CASES_RUNTIME]
    for i, case in enumerate(cases):
        parts.append(f"static const long double cv_expect_{i} = ({case['expect']});")
        parts.append(
            f"static int cv_case_{i}(void) {{\n"
            f"    {case.get('setup', '')}\n"
            f"    __typeof__({case['call']}) cv_actual = ({case['call']});\n"
            f"    int ok = (cv_actual == ({case['expect']}));\n"
            f'    printf("{{\\"case\\":{i},\\"ok\\":%d,\\"actual\\":\\"", ok); CV_SHOW(cv_actual);\n'
            f'    printf("\\"}}\\n"); fflush(stdout);\n'
            f"    return ok;\n}}"
        )
    run = [
        f"    {{ fflush(NULL); pid_t p = fork(); if (p == 0) _exit(cv_case_{i}() ? 0 : 1);\n"
        f"      int s = 0; waitpid(p, &s, 0);\n"
        f'      if (WIFSIGNALED(s)) printf("{{\\"case\\":{i},\\"ok\\":0,\\"crash\\":%d}}\\n", WTERMSIG(s)); }}'
        for i in range(len(cases))
    ]
    parts.append("int main(void) {\n" + "\n".join(run) + "\n    return 0;\n}")
    return write(ctx, f"{artifact['id']}.cases.c", "\n".join(parts) + "\n")
