---
name: Add algorithm core
about: Integrate a PQC hardware implementation with the test kit
title: 'core: [ALGORITHM] on [FPGA]'
labels: algorithm-core
---

## Algorithm

- **Algorithm**: ML-KEM / ML-DSA / SLH-DSA / Other
- **Security levels**: 
- **Operations**: keygen / encaps / decaps / sign / verify

## Implementation source

- **Source**: (link to paper, GitHub repo, or "original implementation")
- **Language**: Verilog / SystemVerilog / VHDL
- **License**: 

## Target FPGA

- **Board**: 
- **Part**: 

## Synthesis results (if available)

- **LUTs**: 
- **FFs**: 
- **BRAMs**: 
- **DSPs**: 
- **Clock (MHz)**: 
- **Keygen cycles**: 
- **Encaps/Sign cycles**: 
- **Decaps/Verify cycles**: 

## Integration status

- [ ] Core implements the pqc-testkit CSR register map
- [ ] Core uses pqc_axi_csr.sv for bus interface
- [ ] Validated with `pqc-testkit fpga` command
- [ ] KAT vectors pass
