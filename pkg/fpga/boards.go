package fpga

// Board describes an FPGA development board's key specifications and
// resource counts. This is used for resource estimation and compatibility
// checks before synthesis.
type Board struct {
	// Name is the human-readable board name (e.g., "Arty A7-35T").
	Name string

	// Vendor is the FPGA vendor ("xilinx" or "intel").
	Vendor string

	// Family is the FPGA device family (e.g., "Artix-7", "Cyclone V").
	Family string

	// Part is the exact FPGA part number (e.g., "xc7a35ticsg324-1L").
	Part string

	// LUTs is the number of look-up tables available.
	LUTs int

	// FFs is the number of flip-flops available.
	FFs int

	// DSPs is the number of DSP slices/blocks available.
	DSPs int

	// BRAMs is the number of Block RAM (18Kb equivalent) tiles.
	BRAMs int

	// MaxClockMHz is the typical achievable clock frequency in MHz.
	MaxClockMHz int

	// HasPCIe indicates whether the board has a PCIe connector.
	HasPCIe bool

	// HasUART indicates whether the board has a USB-UART bridge.
	HasUART bool

	// Interfaces lists available high-speed interfaces.
	Interfaces []string

	// Notes contains additional board-specific information.
	Notes string
}

// XilinxBoards contains the supported Xilinx/AMD FPGA boards, selected
// for their prevalence in PQC research and production deployments.
var XilinxBoards = map[string]Board{
	"arty-a7-35t": {
		Name:        "Arty A7-35T",
		Vendor:      "xilinx",
		Family:      "Artix-7",
		Part:        "xc7a35ticsg324-1L",
		LUTs:        20800,
		FFs:         41600,
		DSPs:        90,
		BRAMs:       100,
		MaxClockMHz: 166,
		HasPCIe:     false,
		HasUART:     true,
		Interfaces:  []string{"UART", "SPI", "I2C", "Pmod", "Arduino"},
		Notes:       "Entry-level. Good for constrained PQC designs and IoT targets.",
	},
	"arty-a7-100t": {
		Name:        "Arty A7-100T",
		Vendor:      "xilinx",
		Family:      "Artix-7",
		Part:        "xc7a100tcsg324-1",
		LUTs:        63400,
		FFs:         126800,
		DSPs:        240,
		BRAMs:       270,
		MaxClockMHz: 200,
		HasPCIe:     false,
		HasUART:     true,
		Interfaces:  []string{"UART", "SPI", "I2C", "Pmod", "Arduino"},
		Notes:       "Mid-range Artix. Fits full ML-KEM + ML-DSA at all security levels.",
	},
	"nexys-a7": {
		Name:        "Nexys A7 (200T)",
		Vendor:      "xilinx",
		Family:      "Artix-7",
		Part:        "xc7a200tsbg484-1",
		LUTs:        134600,
		FFs:         269200,
		DSPs:        740,
		BRAMs:       730,
		MaxClockMHz: 200,
		HasPCIe:     false,
		HasUART:     true,
		Interfaces:  []string{"UART", "VGA", "USB-HID", "Ethernet", "microSD"},
		Notes:       "Most cited Artix-7 board in PQC literature. Academic benchmark standard.",
	},
	"zcu102": {
		Name:        "ZCU102",
		Vendor:      "xilinx",
		Family:      "Zynq UltraScale+",
		Part:        "xczu9eg-ffvb1156-2-e",
		LUTs:        274080,
		FFs:         548160,
		DSPs:        2520,
		BRAMs:       1824,
		MaxClockMHz: 300,
		HasPCIe:     true,
		HasUART:     true,
		Interfaces:  []string{"PCIe Gen2 x4", "UART", "USB3", "DisplayPort", "Ethernet", "FMC"},
		Notes:       "ARM Cortex-A53 SoC + FPGA. Ideal for HW/SW co-design. Go runs on the ARM PS.",
	},
	"alveo-u250": {
		Name:        "Alveo U250",
		Vendor:      "xilinx",
		Family:      "Virtex UltraScale+",
		Part:        "xcu250-figd2104-2L-e",
		LUTs:        1182240,
		FFs:         2364480,
		DSPs:        12288,
		BRAMs:       5376,
		MaxClockMHz: 300,
		HasPCIe:     true,
		HasUART:     false,
		Interfaces:  []string{"PCIe Gen3 x16", "QSFP28 100GbE", "HBM2"},
		Notes:       "Data center card. Production PQC TLS offload. Multi-algorithm parallel.",
	},
}

// IntelBoards contains the supported Intel/Altera FPGA boards.
var IntelBoards = map[string]Board{
	"de10-nano": {
		Name:        "DE10-Nano",
		Vendor:      "intel",
		Family:      "Cyclone V SE",
		Part:        "5CSEBA6U23I7",
		LUTs:        41910,
		FFs:         83820,
		DSPs:        112,
		BRAMs:       553,
		MaxClockMHz: 200,
		HasPCIe:     false,
		HasUART:     true,
		Interfaces:  []string{"UART", "HDMI", "GPIO", "Arduino", "ADC"},
		Notes:       "ARM Cortex-A9 HPS + FPGA. Low-cost SoC for PQC prototyping.",
	},
	"de10-agilex": {
		Name:        "DE10-Agilex",
		Vendor:      "intel",
		Family:      "Agilex 7",
		Part:        "AGFB014R24B2E2V",
		LUTs:        487200,
		FFs:         974400,
		DSPs:        3528,
		BRAMs:       6480,
		MaxClockMHz: 450,
		HasPCIe:     true,
		HasUART:     true,
		Interfaces:  []string{"PCIe Gen4 x16", "QSFP-DD 400GbE", "UART", "FMC+"},
		Notes:       "Latest Intel FPGA. Best-in-class DSP performance for NTT acceleration.",
	},
	"stratix10-dx": {
		Name:        "Stratix 10 DX Dev Kit",
		Vendor:      "intel",
		Family:      "Stratix 10 DX",
		Part:        "1SD280PT2F55E1VG",
		LUTs:        933120,
		FFs:         1866240,
		DSPs:        5760,
		BRAMs:       11721,
		MaxClockMHz: 400,
		HasPCIe:     true,
		HasUART:     true,
		Interfaces:  []string{"PCIe Gen4 x16", "100GbE", "HBM2", "UART"},
		Notes:       "Intel data center FPGA. PCIe Gen4 with P-tile. Comparable to Alveo U250.",
	},
}

// AllBoards returns a combined map of all supported boards across vendors.
func AllBoards() map[string]Board {
	all := make(map[string]Board, len(XilinxBoards)+len(IntelBoards))
	for k, v := range XilinxBoards {
		all[k] = v
	}
	for k, v := range IntelBoards {
		all[k] = v
	}
	return all
}
