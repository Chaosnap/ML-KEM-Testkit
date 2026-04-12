// arty_a7_top.sv - Board-level wrapper for Arty A7-35T / A7-100T
//
// Wraps pqc_mlkem_top with a UART-to-AXI bridge for communication
// with the pqc-testkit host tool.
//
// Pin assignments:
//   - UART TX/RX via USB-UART bridge (directly on board)
//   - LED[0]: busy indicator
//   - LED[1]: done indicator
//   - LED[2]: error indicator
//   - LED[3]: heartbeat (toggles every ~0.5s)
//   - BTN[0]: reset
//
// Build:
//   vivado -mode batch -source scripts/vivado_build.tcl -tclargs arty-a7-35t mlkem
//
// Test:
//   ./pqc-testkit fpga -T uart -d /dev/ttyUSB0 -b 115200

module arty_a7_top (
    input  logic       clk_100mhz,    // 100 MHz oscillator.
    input  logic       rst_n_btn,     // BTN[0] active-low reset.

    // UART (directly connected to USB-UART bridge on board).
    input  logic       uart_rxd,      // UART receive.
    output logic       uart_txd,      // UART transmit.

    // LEDs for status indication.
    output logic [3:0] led
);

    // =========================================================================
    // Clock and reset
    // =========================================================================

    logic clk;
    logic rst_n;
    logic [2:0] rst_sync;

    assign clk = clk_100mhz;

    // Synchronize and debounce reset button.
    always_ff @(posedge clk) begin
        rst_sync <= {rst_sync[1:0], rst_n_btn};
    end
    assign rst_n = rst_sync[2];

    // =========================================================================
    // UART-to-AXI bridge
    // =========================================================================

    // The UART bridge translates the pqc-testkit serial protocol
    // (CMD/ADDR/LEN/DATA/CRC frames) into AXI-Lite read/write transactions.
    //
    // This is a simplified bridge that handles register access.
    // For a production design, use Xilinx AXI UART Lite IP or a custom bridge.

    logic [15:0] axi_awaddr;
    logic        axi_awvalid, axi_awready;
    logic [31:0] axi_wdata;
    logic [3:0]  axi_wstrb;
    logic        axi_wvalid, axi_wready;
    logic [1:0]  axi_bresp;
    logic        axi_bvalid, axi_bready;
    logic [15:0] axi_araddr;
    logic        axi_arvalid, axi_arready;
    logic [31:0] axi_rdata;
    logic [1:0]  axi_rresp;
    logic        axi_rvalid, axi_rready;

    // UART RX/TX signals.
    logic [7:0]  rx_byte;
    logic        rx_valid;
    logic [7:0]  tx_byte;
    logic        tx_valid;
    logic        tx_ready;

    // UART receiver.
    uart_rx #(.CLK_FREQ(100_000_000), .BAUD_RATE(115200)) u_rx (
        .clk      (clk),
        .rst_n    (rst_n),
        .rx       (uart_rxd),
        .data     (rx_byte),
        .valid    (rx_valid)
    );

    // UART transmitter.
    uart_tx #(.CLK_FREQ(100_000_000), .BAUD_RATE(115200)) u_tx (
        .clk      (clk),
        .rst_n    (rst_n),
        .tx       (uart_txd),
        .data     (tx_byte),
        .valid    (tx_valid),
        .ready    (tx_ready)
    );

    // Protocol bridge: converts UART frames to AXI transactions.
    uart_axi_bridge u_bridge (
        .clk           (clk),
        .rst_n         (rst_n),
        .rx_byte       (rx_byte),
        .rx_valid      (rx_valid),
        .tx_byte       (tx_byte),
        .tx_valid      (tx_valid),
        .tx_ready      (tx_ready),
        .m_axi_awaddr  (axi_awaddr),
        .m_axi_awvalid (axi_awvalid),
        .m_axi_awready (axi_awready),
        .m_axi_wdata   (axi_wdata),
        .m_axi_wstrb   (axi_wstrb),
        .m_axi_wvalid  (axi_wvalid),
        .m_axi_wready  (axi_wready),
        .m_axi_bresp   (axi_bresp),
        .m_axi_bvalid  (axi_bvalid),
        .m_axi_bready  (axi_bready),
        .m_axi_araddr  (axi_araddr),
        .m_axi_arvalid (axi_arvalid),
        .m_axi_arready (axi_arready),
        .m_axi_rdata   (axi_rdata),
        .m_axi_rresp   (axi_rresp),
        .m_axi_rvalid  (axi_rvalid),
        .m_axi_rready  (axi_rready)
    );

    // =========================================================================
    // PQC ML-KEM accelerator core
    // =========================================================================

    logic status_busy, status_done, status_error;

    pqc_mlkem_top #(
        .AXI_ADDR_WIDTH (16)
    ) u_mlkem (
        .clk            (clk),
        .rst_n          (rst_n),
        .s_axi_awaddr   (axi_awaddr),
        .s_axi_awvalid  (axi_awvalid),
        .s_axi_awready  (axi_awready),
        .s_axi_wdata    (axi_wdata),
        .s_axi_wstrb    (axi_wstrb),
        .s_axi_wvalid   (axi_wvalid),
        .s_axi_wready   (axi_wready),
        .s_axi_bresp    (axi_bresp),
        .s_axi_bvalid   (axi_bvalid),
        .s_axi_bready   (axi_bready),
        .s_axi_araddr   (axi_araddr),
        .s_axi_arvalid  (axi_arvalid),
        .s_axi_arready  (axi_arready),
        .s_axi_rdata    (axi_rdata),
        .s_axi_rresp    (axi_rresp),
        .s_axi_rvalid   (axi_rvalid),
        .s_axi_rready   (axi_rready),
        .irq            (),  // Not connected on Arty.
        .o_busy         (status_busy),
        .o_done         (status_done),
        .o_error        (status_error)
    );

    // =========================================================================
    // LED indicators
    // =========================================================================

    // Heartbeat counter.
    logic [25:0] heartbeat_cnt;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            heartbeat_cnt <= '0;
        else
            heartbeat_cnt <= heartbeat_cnt + 1;
    end

    assign led[0] = status_busy;            // LED0: operation in progress.
    assign led[1] = status_done;            // LED1: operation complete.
    assign led[2] = status_error;           // LED2: error.
    assign led[3] = heartbeat_cnt[25];      // LED3: heartbeat (~1.5 Hz).

endmodule
