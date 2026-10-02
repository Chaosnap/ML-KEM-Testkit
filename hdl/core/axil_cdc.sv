// axil_cdc.sv - AXI4-Lite clock-domain crossing (one transaction per channel)
//
// Connects an AXI-Lite master in clock domain S (s_clk) to a slave in
// clock domain M (m_clk), e.g. the 100 MHz UART bridge to the ML-KEM core
// running from its own faster clock. Vendor-agnostic: toggle handshakes
// through two-flop synchronisers, no FIFOs or vendor primitives.
//
// Write: AW and W are captured in S (in either order), a request toggle is
// sent to M, which replays them as an AXI-Lite write and returns BRESP with
// an acknowledge toggle; S then presents B. Reads work the same way with
// AR / R. Reads and writes are independent; each has at most one
// transaction in flight, which is all the UART bridge ever issues.
//
// Every multi-bit value that crosses (address, data, strobes, response) is
// held stable in a register of the sending domain from before its toggle
// is sent until the toggle is acknowledged, so only the toggles need
// synchronising. Timing constraints: treat the two clocks as asynchronous
// (set_clock_groups -asynchronous), see hdl/xilinx/arty_a7/constraints.xdc.

module axil_cdc #(
    parameter int ADDR_WIDTH = 16
) (
    // Domain S: slave port (from the master).
    input  logic                  s_clk,
    input  logic                  s_rst_n,
    input  logic [ADDR_WIDTH-1:0] s_axi_awaddr,
    input  logic                  s_axi_awvalid,
    output logic                  s_axi_awready,
    input  logic [31:0]           s_axi_wdata,
    input  logic [3:0]            s_axi_wstrb,
    input  logic                  s_axi_wvalid,
    output logic                  s_axi_wready,
    output logic [1:0]            s_axi_bresp,
    output logic                  s_axi_bvalid,
    input  logic                  s_axi_bready,
    input  logic [ADDR_WIDTH-1:0] s_axi_araddr,
    input  logic                  s_axi_arvalid,
    output logic                  s_axi_arready,
    output logic [31:0]           s_axi_rdata,
    output logic [1:0]            s_axi_rresp,
    output logic                  s_axi_rvalid,
    input  logic                  s_axi_rready,

    // Domain M: master port (to the slave).
    input  logic                  m_clk,
    input  logic                  m_rst_n,
    output logic [ADDR_WIDTH-1:0] m_axi_awaddr,
    output logic                  m_axi_awvalid,
    input  logic                  m_axi_awready,
    output logic [31:0]           m_axi_wdata,
    output logic [3:0]            m_axi_wstrb,
    output logic                  m_axi_wvalid,
    input  logic                  m_axi_wready,
    input  logic [1:0]            m_axi_bresp,
    input  logic                  m_axi_bvalid,
    output logic                  m_axi_bready,
    output logic [ADDR_WIDTH-1:0] m_axi_araddr,
    output logic                  m_axi_arvalid,
    input  logic                  m_axi_arready,
    input  logic [31:0]           m_axi_rdata,
    input  logic [1:0]            m_axi_rresp,
    input  logic                  m_axi_rvalid,
    output logic                  m_axi_rready
);

    // =====================================================================
    // Domain S
    // =====================================================================

    // Write channel.
    logic [ADDR_WIDTH-1:0] s_awaddr_q;
    logic [31:0]           s_wdata_q;
    logic [3:0]            s_wstrb_q;
    logic                  s_aw_got, s_w_got, s_wpend;
    logic                  s_wreq_t;            // Toggles once per write.
    (* ASYNC_REG = "TRUE" *) logic [1:0] s_wack_sync;
    logic                  s_wack_seen;

    // Read channel.
    logic [ADDR_WIDTH-1:0] s_araddr_q;
    logic                  s_ar_got;
    logic                  s_rreq_t;
    (* ASYNC_REG = "TRUE" *) logic [1:0] s_rack_sync;
    logic                  s_rack_seen;

    // Values returned by domain M (stable while its acknowledge is pending).
    logic [1:0]  m_bresp_q, m_rresp_q;
    logic [31:0] m_rdata_q;
    logic        m_wack_t, m_rack_t;

    assign s_axi_awready = !s_aw_got && !s_axi_bvalid;
    assign s_axi_wready  = !s_w_got  && !s_axi_bvalid;
    assign s_axi_arready = !s_ar_got && !s_axi_rvalid;

    always_ff @(posedge s_clk or negedge s_rst_n) begin
        if (!s_rst_n) begin
            s_aw_got     <= 1'b0;
            s_w_got      <= 1'b0;
            s_wpend      <= 1'b0;
            s_wreq_t     <= 1'b0;
            s_wack_sync  <= '0;
            s_wack_seen  <= 1'b0;
            s_axi_bvalid <= 1'b0;
            s_axi_bresp  <= '0;
            s_ar_got     <= 1'b0;
            s_rreq_t     <= 1'b0;
            s_rack_sync  <= '0;
            s_rack_seen  <= 1'b0;
            s_axi_rvalid <= 1'b0;
            s_axi_rresp  <= '0;
            s_axi_rdata  <= '0;
            s_awaddr_q   <= '0;
            s_wdata_q    <= '0;
            s_wstrb_q    <= '0;
            s_araddr_q   <= '0;
        end else begin
            s_wack_sync <= {s_wack_sync[0], m_wack_t};
            s_rack_sync <= {s_rack_sync[0], m_rack_t};

            // Write: collect AW and W, then request.
            if (s_axi_awvalid && s_axi_awready) begin
                s_awaddr_q <= s_axi_awaddr;
                s_aw_got   <= 1'b1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                s_wdata_q <= s_axi_wdata;
                s_wstrb_q <= s_axi_wstrb;
                s_w_got   <= 1'b1;
            end
            if (s_aw_got && s_w_got && !s_wpend && !s_axi_bvalid) begin
                s_wreq_t <= !s_wreq_t;
                s_wpend  <= 1'b1;
            end
            if (s_wpend && (s_wack_sync[1] != s_wack_seen)) begin
                s_wack_seen  <= s_wack_sync[1];
                s_wpend      <= 1'b0;
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= m_bresp_q;
            end
            if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
                s_aw_got     <= 1'b0;
                s_w_got      <= 1'b0;
            end

            // Read.
            if (s_axi_arvalid && s_axi_arready) begin
                s_araddr_q <= s_axi_araddr;
                s_ar_got   <= 1'b1;
                s_rreq_t   <= !s_rreq_t;
            end
            if (s_ar_got && !s_axi_rvalid && (s_rack_sync[1] != s_rack_seen)) begin
                s_rack_seen  <= s_rack_sync[1];
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= m_rdata_q;
                s_axi_rresp  <= m_rresp_q;
            end
            if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
                s_ar_got     <= 1'b0;
            end
        end
    end

    // =====================================================================
    // Domain M
    // =====================================================================

    (* max_fanout = 32 *) logic m_srst;     // Registered synchronous reset (M).
    always_ff @(posedge m_clk)
        m_srst <= !m_rst_n;

    (* ASYNC_REG = "TRUE" *) logic [1:0] m_wreq_sync, m_rreq_sync;
    logic m_wreq_seen, m_rreq_seen;
    logic m_wbusy, m_rbusy;

    assign m_axi_bready = m_wbusy && !m_axi_awvalid && !m_axi_wvalid;
    assign m_axi_rready = m_rbusy && !m_axi_arvalid;

    always_ff @(posedge m_clk) begin               // Synchronous reset.
        if (m_srst) begin
            m_wreq_sync   <= '0;
            m_rreq_sync   <= '0;
            m_wreq_seen   <= 1'b0;
            m_rreq_seen   <= 1'b0;
            m_wbusy       <= 1'b0;
            m_rbusy       <= 1'b0;
            m_wack_t      <= 1'b0;
            m_rack_t      <= 1'b0;
            m_axi_awvalid <= 1'b0;
            m_axi_wvalid  <= 1'b0;
            m_axi_arvalid <= 1'b0;
            m_axi_awaddr  <= '0;
            m_axi_wdata   <= '0;
            m_axi_wstrb   <= '0;
            m_axi_araddr  <= '0;
            m_bresp_q     <= '0;
            m_rresp_q     <= '0;
            m_rdata_q     <= '0;
        end else begin
            m_wreq_sync <= {m_wreq_sync[0], s_wreq_t};
            m_rreq_sync <= {m_rreq_sync[0], s_rreq_t};

            // Write replay.
            if (!m_wbusy && (m_wreq_sync[1] != m_wreq_seen)) begin
                m_wreq_seen   <= m_wreq_sync[1];
                m_wbusy       <= 1'b1;
                m_axi_awaddr  <= s_awaddr_q;
                m_axi_wdata   <= s_wdata_q;
                m_axi_wstrb   <= s_wstrb_q;
                m_axi_awvalid <= 1'b1;
                m_axi_wvalid  <= 1'b1;
            end
            if (m_axi_awvalid && m_axi_awready) m_axi_awvalid <= 1'b0;
            if (m_axi_wvalid  && m_axi_wready)  m_axi_wvalid  <= 1'b0;
            if (m_axi_bready && m_axi_bvalid) begin
                m_bresp_q <= m_axi_bresp;
                m_wack_t  <= !m_wack_t;
                m_wbusy   <= 1'b0;
            end

            // Read replay.
            if (!m_rbusy && (m_rreq_sync[1] != m_rreq_seen)) begin
                m_rreq_seen   <= m_rreq_sync[1];
                m_rbusy       <= 1'b1;
                m_axi_araddr  <= s_araddr_q;
                m_axi_arvalid <= 1'b1;
            end
            if (m_axi_arvalid && m_axi_arready) m_axi_arvalid <= 1'b0;
            if (m_axi_rready && m_axi_rvalid) begin
                m_rdata_q <= m_axi_rdata;
                m_rresp_q <= m_axi_rresp;
                m_rack_t  <= !m_rack_t;
                m_rbusy   <= 1'b0;
            end
        end
    end

endmodule
