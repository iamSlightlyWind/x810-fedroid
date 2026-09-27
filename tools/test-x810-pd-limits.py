#!/usr/bin/env python3
"""Compile and exercise the shared X810 fixed-PD/PPS contract limits."""

from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "kernel/files/x810_pd_limits.h"


def main() -> None:
    source = r'''#include <stdio.h>
#include "x810_pd_limits.h"
struct test_case { int direct; unsigned int mv, ma; int valid; };
static const struct test_case cases[] = {
    { 0, 0, 0, 1 },
    { 0, 0, 3000, 1 },
    { 0, 0, 3001, 0 },
    { 0, 4999, 500, 0 },
    { 0, 5000, 3000, 1 },
    { 0, 9000, 3000, 1 },
    { 0, 9001, 1000, 0 },
    { 0, 9000, 3001, 0 },
    { 1, 0, 3000, 1 },
    { 1, 0, 3001, 0 },
    { 1, 5000, 5000, 1 },
    { 1, 10500, 5000, 1 },
    { 1, 10501, 3000, 0 },
    { 1, 10500, 5001, 0 },
};
int main(void) {
    unsigned int i;
    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        int got = x810_pd_contract_is_valid(cases[i].direct,
                                            cases[i].mv, cases[i].ma);
        if (got != cases[i].valid) {
            fprintf(stderr, "case %u: direct=%d mv=%u ma=%u: got %d, want %d\n",
                    i, cases[i].direct, cases[i].mv, cases[i].ma,
                    got, cases[i].valid);
            return 1;
        }
    }
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix="x810-pd-test-") as temp:
        temp_path = Path(temp)
        c_file = temp_path / "test.c"
        binary = temp_path / "test"
        c_file.write_text(source)
        subprocess.run(
            ["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
             "-I", str(HEADER.parent), str(c_file), "-o", str(binary)],
            check=True,
        )
        subprocess.run([str(binary)], check=True)
    print("X810 fixed-PD/PPS contract boundaries passed")


if __name__ == "__main__":
    main()
