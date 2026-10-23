// SPDX-License-Identifier: MIT
// Kintex-7 ML-KEM top for the pinned sakura-x-shell Spartan-6 controller.
// Native parallel FIFO bridge, no UART serialization in the Kintex datapath.
module sakura_x_top (
    input logic clk200_p, clk200_n, pushsw,
    input logic bus_clk, i_bus_reset_n,
    input logic [31:0] i_common,
    input logic i_addr_valid, i_write_enable, i_write_data_valid, i_read_data_ready,
    output logic [31:0] o_read_data,
    output logic o_addr_ready, o_write_data_ready, o_read_data_valid,
    output logic osc_en_b, trig_out,
    output logic [9:0] led
);
    logic clk200, clk, clk_core, core_raw, fb_raw, fb, locked;
    logic bus_clk_in, bus_raw, bus_fb_raw, bus_fb, bus_locked;
    IBUFDS u_osc (.I(clk200_p), .IB(clk200_n), .O(clk200));
    // Deskew the forwarded clock against the clock at the input pin. A direct
    // IBUF -> BUFG left ~4.9 ns of uncompensated insertion delay on the routed
    // K7 -> S6 paths, which only have half a 50 MHz cycle for launch/capture.
    // The feedback BUFG is essential: do not bypass it or put a BUFG before
    // this MMCM. Input and output remain 50 MHz; VCO = 50 * 20 = 1000 MHz.
    IBUF u_bus_in (.I(bus_clk), .O(bus_clk_in));
    MMCME2_BASE #(
        .CLKIN1_PERIOD(20.0), .DIVCLK_DIVIDE(1),
        .CLKFBOUT_MULT_F(20.0), .CLKOUT0_DIVIDE_F(20.0),
        .CLKFBOUT_PHASE(0.0), .CLKOUT0_PHASE(0.0)
    ) u_bus_mmcm (
        .CLKIN1(bus_clk_in), .CLKFBIN(bus_fb), .CLKFBOUT(bus_fb_raw),
        .CLKOUT0(bus_raw), .LOCKED(bus_locked), .RST(pushsw), .PWRDWN(1'b0),
        .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(), .CLKOUT2(),
        .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6()
    );
    BUFG u_bus_fb (.I(bus_fb_raw), .O(bus_fb));
    BUFG u_bus (.I(bus_raw), .O(clk));
    MMCME2_BASE #(
        .CLKIN1_PERIOD(5.0), .DIVCLK_DIVIDE(1),
        .CLKFBOUT_MULT_F(4.125), .CLKOUT0_DIVIDE_F(4.0)
    ) u_mmcm (
        .CLKIN1(clk200), .CLKFBIN(fb), .CLKFBOUT(fb_raw),
        .CLKOUT0(core_raw), .LOCKED(locked), .RST(pushsw), .PWRDWN(1'b0),
        .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(), .CLKOUT2(),
        .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6()
    );
    BUFG u_fb (.I(fb_raw), .O(fb));
    BUFG u_core_clk (.I(core_raw), .O(clk_core));
    assign osc_en_b = 1'b1;
    // An RTS/soft reset leaves both clock managers running. Loss of either
    // lock resets both sides of the AXI CDC; each side releases synchronously.
    wire arst = !locked || !bus_locked || !i_bus_reset_n || pushsw;
    (* ASYNC_REG = "TRUE" *) logic [3:0] reset_bus, reset_core;
    always_ff @(posedge clk or posedge arst)
        if (arst) reset_bus <= 0;
        else reset_bus <= {reset_bus[2:0], 1'b1};
    always_ff @(posedge clk_core or posedge arst)
        if (arst) reset_core <= 0;
        else reset_core <= {reset_core[2:0], 1'b1};
    wire rst_n = reset_bus[3], core_rst_n = reset_core[3];

    // Capture all S6 signals together in ILOGIC, then use a full cycle inside
    // the bridge. The upstream controller holds common/valid until ACK. Its
    // one-cycle read ACK is also captured here, so it cannot be skipped by the
    // added pipeline stage. These are synchronous I/O registers, not CDC FFs.
    (* IOB = "TRUE" *) logic [31:0] common_q;
    (* IOB = "TRUE" *) logic addr_valid_q, write_enable_q;
    (* IOB = "TRUE" *) logic write_valid_q, read_ready_q;
    // Register data and its valid together in OLOGIC. No FSM decode or fabric
    // route remains after the output FF. This adds one bus cycle of response
    // latency; the four-phase handshake waits for the peer to drop its valid.
    (* IOB = "TRUE" *) logic [31:0] read_data_q;
    (* IOB = "TRUE" *) logic addr_ready_q, write_ready_q, read_valid_q;
    logic [31:0] bridge_read_data;
    logic bridge_addr_ready, bridge_write_ready, bridge_read_valid;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            common_q <= 0;
            addr_valid_q <= 0;
            write_enable_q <= 0;
            write_valid_q <= 0;
            read_ready_q <= 0;
            read_data_q <= 0;
            addr_ready_q <= 0;
            write_ready_q <= 0;
            read_valid_q <= 0;
        end else begin
            common_q <= i_common;
            addr_valid_q <= i_addr_valid;
            write_enable_q <= i_write_enable;
            write_valid_q <= i_write_data_valid;
            read_ready_q <= i_read_data_ready;
            read_data_q <= bridge_read_data;
            addr_ready_q <= bridge_addr_ready;
            write_ready_q <= bridge_write_ready;
            read_valid_q <= bridge_read_valid;
        end
    end
    assign o_read_data = read_data_q;
    assign o_addr_ready = addr_ready_q;
    assign o_write_data_ready = write_ready_q;
    assign o_read_data_valid = read_valid_q;

    logic [15:0] awaddr, araddr;
    logic awvalid, awready, wvalid, wready, bvalid, bready;
    logic arvalid, arready, rvalid, rready;
    logic [31:0] wdata, rdata;
    logic [3:0] wstrb;
    logic [1:0] bresp, rresp;
    logic status_busy, status_done, status_error, run_active;
    logic [1:0] op_mode;

    sakura_x_bridge u_bridge (
        .clk(clk), .rst_n(rst_n),
        .i_addr_valid(addr_valid_q), .i_write_enable(write_enable_q),
        .i_write_data_valid(write_valid_q), .i_read_data_ready(read_ready_q),
        .i_common(common_q), .o_read_data(bridge_read_data), .o_addr_ready(bridge_addr_ready),
        .o_write_data_ready(bridge_write_ready), .o_read_data_valid(bridge_read_valid),
        .core_busy(status_busy), .core_error(status_error), .run_active(run_active),
        .m_axi_awaddr(awaddr), .m_axi_awvalid(awvalid), .m_axi_awready(awready),
        .m_axi_wdata(wdata), .m_axi_wstrb(wstrb), .m_axi_wvalid(wvalid), .m_axi_wready(wready),
        .m_axi_bresp(bresp), .m_axi_bvalid(bvalid), .m_axi_bready(bready),
        .m_axi_araddr(araddr), .m_axi_arvalid(arvalid), .m_axi_arready(arready),
        .m_axi_rdata(rdata), .m_axi_rresp(rresp), .m_axi_rvalid(rvalid), .m_axi_rready(rready)
    );
    logic [15:0] c_awaddr, c_araddr;
    logic        c_awvalid, c_awready, c_wvalid, c_wready, c_bvalid, c_bready;
    logic        c_arvalid, c_arready, c_rvalid, c_rready;
    logic [31:0] c_wdata, c_rdata;
    logic [3:0]  c_wstrb;
    logic [1:0]  c_bresp, c_rresp;

    axil_cdc #(.ADDR_WIDTH (16)) u_cdc (
        .s_clk (clk), .s_rst_n (rst_n),
        .s_axi_awaddr (awaddr), .s_axi_awvalid (awvalid), .s_axi_awready (awready),
        .s_axi_wdata (wdata), .s_axi_wstrb (wstrb), .s_axi_wvalid (wvalid), .s_axi_wready (wready),
        .s_axi_bresp (bresp), .s_axi_bvalid (bvalid), .s_axi_bready (bready),
        .s_axi_araddr (araddr), .s_axi_arvalid (arvalid), .s_axi_arready (arready),
        .s_axi_rdata (rdata), .s_axi_rresp (rresp), .s_axi_rvalid (rvalid), .s_axi_rready (rready),
        .m_clk (clk_core), .m_rst_n (core_rst_n),
        .m_axi_awaddr (c_awaddr), .m_axi_awvalid (c_awvalid), .m_axi_awready (c_awready),
        .m_axi_wdata (c_wdata), .m_axi_wstrb (c_wstrb), .m_axi_wvalid (c_wvalid), .m_axi_wready (c_wready),
        .m_axi_bresp (c_bresp), .m_axi_bvalid (c_bvalid), .m_axi_bready (c_bready),
        .m_axi_araddr (c_araddr), .m_axi_arvalid (c_arvalid), .m_axi_arready (c_arready),
        .m_axi_rdata (c_rdata), .m_axi_rresp (c_rresp), .m_axi_rvalid (c_rvalid), .m_axi_rready (c_rready)
    );

    pqc_mlkem_top #(.AXI_ADDR_WIDTH (16)) u_mlkem (
        .clk (clk_core), .rst_n (core_rst_n),
        .s_axi_awaddr (c_awaddr), .s_axi_awvalid (c_awvalid), .s_axi_awready (c_awready),
        .s_axi_wdata (c_wdata), .s_axi_wstrb (c_wstrb), .s_axi_wvalid (c_wvalid), .s_axi_wready (c_wready),
        .s_axi_bresp (c_bresp), .s_axi_bvalid (c_bvalid), .s_axi_bready (c_bready),
        .s_axi_araddr (c_araddr), .s_axi_arvalid (c_arvalid), .s_axi_arready (c_arready),
        .s_axi_rdata (c_rdata), .s_axi_rresp (c_rresp), .s_axi_rvalid (c_rvalid), .s_axi_rready (c_rready),
        .irq (),
        .o_busy (status_busy), .o_done (status_done), .o_error (status_error), .o_op_mode (op_mode)
    );

    assign trig_out = status_busy;
    // Keep indicator activity outside the complete START / capture window.
    (* ASYNC_REG = "TRUE" *) logic [1:0] done_sync, error_sync;
    logic [25:0] heartbeat;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            done_sync <= 0;
            error_sync <= 0;
            heartbeat <= 0;
            led <= 0;
        end else begin
            done_sync <= {done_sync[0], status_done};
            error_sync <= {error_sync[0], status_error};
            if (!run_active) begin
                heartbeat <= heartbeat + 1;
                // LED1 heartbeat, LED2 done, LED3 error, LED10 ready.
                led <= {1'b1, 6'b0, error_sync[1], done_sync[1], heartbeat[25]};
            end
        end
    end
endmodule
