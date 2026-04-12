// Package axi provides AXI memory-mapped communication with PQC accelerator
// cores on Xilinx Zynq and Zynq UltraScale+ SoC FPGAs.
//
// On Zynq platforms, the ARM Processing System (PS) communicates with the
// Programmable Logic (PL) over AXI interconnects. The PQC accelerator cores
// are mapped into the PS address space and accessed via /dev/mem or a UIO
// (Userspace I/O) driver.
//
// # Address Map (Zynq UltraScale+)
//
// The PL AXI peripherals are typically mapped at:
//   - 0xA000_0000 — CSR register block (AXI-Lite)
//   - 0xA001_0000 — Data buffer (AXI Full, for key/ciphertext I/O)
//
// These addresses are defined in the Vivado address editor and must match
// the device tree or UIO configuration.
//
// # Usage
//
//	dev, err := axi.OpenUIO("/dev/uio0", 0x10000)
//	defer dev.Close()
//	val, _ := dev.ReadReg(fpga.RegALGID)
package axi
