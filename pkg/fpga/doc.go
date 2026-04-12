// Package fpga provides transport-agnostic communication with FPGA-based PQC
// accelerators.
//
// The package defines a common [Device] interface that abstracts over the
// physical transport layer (PCIe DMA, AXI memory-mapped, or UART serial).
// All PQC accelerator cores must implement a standard register map accessible
// via the Device interface, regardless of which FPGA vendor or board is used.
//
// # Register Map Convention
//
// The PQC accelerator cores expose a standard AXI-Lite control/status register
// (CSR) block. The base address is transport-dependent, but the register offsets
// are fixed:
//
//	Offset  Name          Access  Description
//	0x00    CTRL          RW      Control register (start, reset, algorithm select)
//	0x04    STATUS        RO      Status register (busy, done, error flags)
//	0x08    ALG_ID        RO      Algorithm identifier (1=ML-KEM, 2=ML-DSA, 3=SLH-DSA)
//	0x0C    SEC_LEVEL     RW      Security level parameter
//	0x10    OP_MODE       RW      Operation mode (0=keygen, 1=encaps/sign, 2=decaps/verify)
//	0x14    CYCLE_COUNT   RO      Hardware cycle counter (latches on done)
//	0x18    VERSION       RO      Core version (major.minor.patch packed as 32-bit)
//	0x1C    ERROR_CODE    RO      Error code from last operation
//	0x20    DATA_IN_ADDR  RW      DMA source address (PCIe) or buffer offset (AXI)
//	0x24    DATA_IN_LEN   RW      Input data length in bytes
//	0x28    DATA_OUT_ADDR RW      DMA destination address or buffer offset
//	0x2C    DATA_OUT_LEN  RO      Output data length in bytes
//
// # Usage
//
//	dev, err := pcie.Open("/dev/xdma0")
//	defer dev.Close()
//	info, _ := fpga.Identify(dev)
//	// info.Algorithm = "ML-KEM", info.Level = 768
package fpga
