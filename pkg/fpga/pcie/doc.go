// Package pcie provides PCIe-based communication with FPGA PQC accelerators.
//
// It supports Xilinx XDMA and Intel Avalon-MM PCIe endpoint cores. The driver
// opens the PCIe BAR regions via /dev/xdma* (Xilinx) or /dev/intel_fpga_pcie*
// (Intel) character devices and maps them for register and DMA access.
//
// # Xilinx XDMA
//
// The Xilinx XDMA driver exposes:
//   - /dev/xdma0_user   — AXI-Lite BAR for CSR access
//   - /dev/xdma0_h2c_0  — Host-to-Card DMA channel
//   - /dev/xdma0_c2h_0  — Card-to-Host DMA channel
//
// # Intel Avalon
//
// The Intel FPGA PCIe driver exposes:
//   - /dev/intel_fpga_pcie0 — unified device node
//
// # Usage
//
//	dev, err := pcie.OpenXDMA("/dev/xdma0")
//	defer dev.Close()
//	val, _ := dev.ReadReg(fpga.RegALGID)
package pcie
