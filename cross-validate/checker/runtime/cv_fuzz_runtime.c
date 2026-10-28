/*
 * Deterministic random testing of SV-COMP style harnesses (design doc §3 流程 6, third method).
 *
 * The harness is compiled with -Dmain=cv_harness_main and linked with this file. Every iteration runs
 * in a forked child, so state left by one iteration never leaks into the next. Values come from a
 * splitmix64 generator seeded with (seed, iteration): the same configuration always draws the same
 * inputs. Limits are counts, not time: CV_FUZZ_ITERATIONS iterations, CV_FUZZ_MAX_DRAWS values per
 * iteration. The alarm is only a safety net against an implementation that never returns.
 *
 *   assert fails (SIGABRT)    -> counterexample: the drawn values and the logged interface calls
 *   __VERIFIER_assume(0)      -> iteration discarded: its input is outside the premises
 *   draw budget reached       -> iteration ends normally
 */
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#define CV_EXIT_ASSUME 77
#define CV_EXIT_TIMEOUT 78

int cv_harness_main(void);

static uint64_t cv_rng;
static unsigned long cv_draws;
static unsigned long cv_max_draws = 64;
FILE *cv_log;

static uint64_t cv_next(void) {
    uint64_t z = (cv_rng += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

/* Log-uniform magnitude, random sign: small values and boundaries are as likely as large ones. */
static uint64_t cv_raw(int bits) {
    uint64_t r = cv_next();
    int width = (int)(cv_next() % (uint64_t)(bits + 1));
    uint64_t v = width >= 64 ? r : (r & ((1ULL << width) - 1ULL));
    if (cv_next() % 16 == 0) v = (cv_next() & 1) ? ~0ULL : (1ULL << (bits - 1));
    return v;
}

static void cv_budget(void) {
    if (++cv_draws > cv_max_draws) {
        fflush(NULL);
        _exit(0);
    }
}

#define CV_SIGNED(name, type, bits)                                       \
    type __VERIFIER_nondet_##name(void) {                                 \
        cv_budget();                                                      \
        uint64_t raw = cv_raw(bits - 1);                                  \
        long long v = (long long)raw;                                     \
        if (cv_next() & 1) v = -v;                                        \
        type out = (type)v;                                               \
        if (cv_log) fprintf(cv_log, "nondet %s %lld\n", #name, (long long)out); \
        return out;                                                       \
    }
#define CV_UNSIGNED(name, type, bits)                                     \
    type __VERIFIER_nondet_##name(void) {                                 \
        cv_budget();                                                      \
        type out = (type)cv_raw(bits);                                    \
        if (cv_log) fprintf(cv_log, "nondet %s %llu\n", #name, (unsigned long long)out); \
        return out;                                                       \
    }

CV_SIGNED(char, char, 8)
CV_SIGNED(short, short, 16)
CV_SIGNED(int, int, 32)
CV_SIGNED(long, long, 64)
CV_SIGNED(longlong, long long, 64)
CV_UNSIGNED(uchar, unsigned char, 8)
CV_UNSIGNED(ushort, unsigned short, 16)
CV_UNSIGNED(uint, unsigned int, 32)
CV_UNSIGNED(unsigned, unsigned int, 32)
CV_UNSIGNED(ulong, unsigned long, 64)
CV_UNSIGNED(ulonglong, unsigned long long, 64)
CV_UNSIGNED(size_t, __SIZE_TYPE__, 64)

_Bool __VERIFIER_nondet_bool(void) {
    cv_budget();
    _Bool out = (_Bool)(cv_next() & 1);
    if (cv_log) fprintf(cv_log, "nondet bool %d\n", (int)out);
    return out;
}

double __VERIFIER_nondet_double(void) {
    cv_budget();
    double out = (double)(long long)cv_raw(52) / (double)(1 + (cv_next() % 1000));
    if (cv_next() & 1) out = -out;
    if (cv_log) fprintf(cv_log, "nondet double %.17g\n", out);
    return out;
}

float __VERIFIER_nondet_float(void) {
    float out = (float)__VERIFIER_nondet_double();
    return out;
}

void __VERIFIER_assume(int cond) {
    if (!cond) {
        fflush(NULL);
        _exit(CV_EXIT_ASSUME);
    }
}

static void cv_on_alarm(int sig) {
    (void)sig;
    _exit(CV_EXIT_TIMEOUT);
}

static unsigned long cv_env(const char *name, unsigned long fallback) {
    const char *v = getenv(name);
    return v && *v ? strtoul(v, NULL, 10) : fallback;
}

int main(void) {
    unsigned long iterations = cv_env("CV_FUZZ_ITERATIONS", 1000);
    unsigned long seed = cv_env("CV_FUZZ_SEED", 1);
    unsigned long safety = cv_env("CV_FUZZ_SAFETY_SECONDS", 10);
    const char *log_path = getenv("CV_FUZZ_LOG");
    cv_max_draws = cv_env("CV_FUZZ_MAX_DRAWS", 64);
    unsigned long valid = 0, discarded = 0, timeouts = 0;
    for (unsigned long i = 0; i < iterations; i++) {
        fflush(NULL);
        pid_t pid = fork();
        if (pid < 0) {
            printf("{\"error\":\"fork failed\"}\n");
            return 2;
        }
        if (pid == 0) {
            cv_rng = (uint64_t)seed * 0x100000001B3ULL ^ (uint64_t)i * 0x9E3779B97F4A7C15ULL;
            cv_log = log_path ? fopen(log_path, "w") : NULL;
            if (cv_log) setvbuf(cv_log, NULL, _IONBF, 0);
            signal(SIGALRM, cv_on_alarm);
            alarm((unsigned)safety);
            cv_harness_main();
            fflush(NULL);
            _exit(0);
        }
        int status = 0;
        waitpid(pid, &status, 0);
        if (WIFEXITED(status) && WEXITSTATUS(status) == CV_EXIT_ASSUME) {
            discarded++;
        } else if (WIFEXITED(status) && WEXITSTATUS(status) == CV_EXIT_TIMEOUT) {
            timeouts++;
        } else if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
            valid++;
        } else {
            int sig = WIFSIGNALED(status) ? WTERMSIG(status) : -1;
            int code = WIFEXITED(status) ? WEXITSTATUS(status) : -1;
            printf("{\"iterations\":%lu,\"valid\":%lu,\"discarded\":%lu,\"timeouts\":%lu,"
                   "\"failed_iteration\":%lu,\"signal\":%d,\"exit_code\":%d}\n",
                   i + 1, valid, discarded, timeouts, i, sig, code);
            return 1;
        }
    }
    printf("{\"iterations\":%lu,\"valid\":%lu,\"discarded\":%lu,\"timeouts\":%lu,\"failed_iteration\":-1}\n",
           iterations, valid, discarded, timeouts);
    return 0;
}
