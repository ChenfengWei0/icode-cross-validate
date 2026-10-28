#!/bin/sh
# External SAT solver for CBMC with a deterministic budget: z3 stops after CV_SAT_CONFLICTS conflicts
# and answers "s UNKNOWN", which the checker reports as an inconclusive result (never as a pass).
exec z3 -dimacs "sat.max_conflicts=${CV_SAT_CONFLICTS:-2000000}" "$@"
