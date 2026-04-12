"""
Cocotb testbench for NTT Barrett reduction modules.

Verifies mod_reduce.sv Barrett reduction against Python reference.

Run with:
    cd testbench/ntt
    make SIM=verilator
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
import random


@cocotb.test()
async def test_barrett_3329_basic(dut):
    """Verify Barrett reduction for q=3329 with known values."""
    Q = 3329

    test_cases = [
        (0, 0),
        (1, 1),
        (3328, 3328),
        (3329, 0),
        (3330, 1),
        (6658, 0),       # 2*q
        (6659, 1),       # 2*q + 1
        (100 * 3329, 0), # Multiple of q
        (100 * 3329 + 42, 42),
    ]

    for x_val, expected in test_cases:
        if x_val >= (1 << 24):
            continue  # Skip values too large for 24-bit input.
        dut.x.value = x_val
        await cocotb.triggers.Timer(1, units="ns")  # Combinational delay.

        result = int(dut.r.value)
        assert result == expected, (
            f"Barrett_3329({x_val}) = {result}, expected {expected}"
        )

    dut._log.info(f"Passed {len(test_cases)} basic test cases for q=3329")


@cocotb.test()
async def test_barrett_3329_random(dut):
    """Verify Barrett reduction for q=3329 with random values."""
    Q = 3329
    random.seed(42)

    for _ in range(1000):
        # Random value in range [0, q^2)
        x_val = random.randint(0, min(Q * Q - 1, (1 << 24) - 1))
        expected = x_val % Q

        dut.x.value = x_val
        await cocotb.triggers.Timer(1, units="ns")

        result = int(dut.r.value)
        assert result == expected, (
            f"Barrett_3329({x_val}) = {result}, expected {expected}"
        )

    dut._log.info("Passed 1000 random test cases for q=3329")
