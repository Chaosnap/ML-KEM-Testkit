// Package sca provides side-channel analysis test pattern generation and
// evaluation tools for PQC FPGA implementations.
//
// Side-channel attacks exploit physical leakage (power consumption, EM
// radiation, timing) from cryptographic hardware. This package generates
// test patterns for common evaluation methodologies:
//
//   - TVLA (Test Vector Leakage Assessment): Welch's t-test between fixed
//     and random input classes to detect first-order leakage.
//   - Correlation Power Analysis (CPA): patterns for correlating power traces
//     with intermediate values in the NTT butterfly operations.
//   - Fault Injection: clock and voltage glitch parameter sweeps for testing
//     fault resistance.
//
// These patterns are designed to be sent to an FPGA target via the fpga
// package and paired with an oscilloscope or logic analyzer for trace capture.
package sca
