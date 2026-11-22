"""Quick RSA parameter sanity checker for CTF crypto triage."""

from __future__ import annotations

import math


def check_rsa_basics(n: int, e: int, c: int) -> dict[str, object]:
    """Inspect bit-length, small exponent condition, and basic gcd hints."""
    n_bits = n.bit_length()
    c_lt_n = c < n
    gcd_ec = math.gcd(e, c)
    return {
        "n_bits": n_bits,
        "e": e,
        "small_e": e <= 17,
        "c_less_than_n": c_lt_n,
        "gcd_e_c": gcd_ec,
    }


if __name__ == "__main__":
    print("Import `check_rsa_basics(n, e, c)` in your solve script.")
