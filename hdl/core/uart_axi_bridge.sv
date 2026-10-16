// uart_axi_bridge.sv - UART-to-AXI-Lite protocol bridge
//
// Translates the host UART protocol into AXI-Lite transactions on
// the accelerator bus: register commands access the CSR space, data
// commands access the data buffer window at DATA_BASE.
//
// Protocol (host side: cpa_toolkit/uart_target.py; full description:
// docs/UART_PROTOCOL.md). All multi-byte fields are little-endian; CRC is
// CRC-32/IEEE (Python zlib.crc32).
//
// Host -> FPGA command frame:
//   [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC:4]   CRC over CMD..DATA
//
//   0x01 Read register   ADDR = bus address, LEN = 0          -> 4 bytes
//   0x02 Write register  ADDR = bus address, LEN = 4, value   -> 0 bytes
//   0x03 Write data      ADDR = buffer offset, LEN = 1..256   -> 0 bytes
//   0x04 Read data       ADDR = buffer offset, LEN = 4,
//                        DATA = u32 byte count (1..16384)     -> count bytes
//   0x05 Run             ADDR = bus address (CTRL), LEN = 12,
//                        DATA = u32 value, u32 out offset,
//                        u32 out count (0..16384)             -> 12 + n bytes
//
// 0x05 writes the value (CTRL.start) and then sends NOTHING until the core
// has finished: it waits RUN_GUARD cycles for core_busy to reach this clock
// domain, then until core_busy is low (or RUN_TIMEOUT_CYCLES). The reply is
// the completion notice: STATUS, CYCLE_COUNT and ERROR_CODE (3 x u32), then
// - only if STATUS = done without error - out count bytes of the data
// buffer from out offset (the result, e.g. the shared secret K). The UART
// is idle in both directions for the whole operation, so no link activity
// overlaps a power trace, and the host needs no STATUS polling. Bytes the
// host sends while a Run is waiting are dropped. A write that starts
// nothing (e.g. to a read-only register, value 0) returns at once: hosts
// use that to detect whether a bitstream supports 0x05.
//
// FPGA -> Host response frame:
//   [STATUS:1][LEN:4][DATA:LEN][CRC:4]        CRC over STATUS..DATA
//
//   STATUS 0x00 OK; 0x01 error (bad CRC, unknown command, bad length,
//   out-of-range buffer access or AXI error; LEN = 0).
//
// A partially received frame is discarded after TIMEOUT_CYCLES without a
// byte, so the host can always resynchronise.

module uart_axi_bridge #(
    parameter int          TIMEOUT_CYCLES = 10_000_000,  // 100 ms at 100 MHz.
    parameter logic [15:0] DATA_BASE      = 16'h4000,    // Bus address of buffer byte 0.
    parameter int          DATA_SIZE      = 16384,       // Buffer size in bytes.
    parameter int          MAX_WRITE      = 256,         // Max 0x03 payload.
    parameter int          RUN_GUARD      = 16,          // 0x05: cycles before core_busy is trusted.
    parameter int          RUN_TIMEOUT_CYCLES = 100_000_000  // 0x05: give up waiting (1 s at 100 MHz).
) (
    input  logic        clk,
    input  logic        rst_n,

    // Core busy flag (STATUS.busy) for 0x05 Run. May come from another
    // clock domain: synchronised here (two flops).
    input  logic        core_busy,

    // UART byte interface.
    input  logic [7:0]  rx_byte,
    input  logic        rx_valid,
    output logic [7:0]  tx_byte,
    output logic        tx_valid,
    input  logic        tx_ready,

    // AXI-Lite master interface (to PQC core).
    output logic [15:0] m_axi_awaddr,
    output logic        m_axi_awvalid,
    input  logic        m_axi_awready,
    output logic [31:0] m_axi_wdata,
    output logic [3:0]  m_axi_wstrb,
    output logic        m_axi_wvalid,
    input  logic        m_axi_wready,
    input  logic [1:0]  m_axi_bresp,
    input  logic        m_axi_bvalid,
    output logic        m_axi_bready,
    output logic [15:0] m_axi_araddr,
    output logic        m_axi_arvalid,
    input  logic        m_axi_arready,
    input  logic [31:0] m_axi_rdata,
    input  logic [1:0]  m_axi_rresp,
    input  logic        m_axi_rvalid,
    output logic        m_axi_rready
);

    localparam logic [7:0] CMD_READ_REG   = 8'h01;
    localparam logic [7:0] CMD_WRITE_REG  = 8'h02;
    localparam logic [7:0] CMD_WRITE_DATA = 8'h03;
    localparam logic [7:0] CMD_READ_DATA  = 8'h04;
    localparam logic [7:0] CMD_RUN        = 8'h05;

    // CSR addresses read back by 0x05 (pqc_axi_csr.sv register map).
    localparam logic [15:0] REG_STATUS      = 16'h0004;
    localparam logic [15:0] REG_CYCLE_COUNT = 16'h0014;
    localparam logic [15:0] REG_ERROR_CODE  = 16'h001C;

    // CRC-32/IEEE, reflected, one byte per call.
    function automatic logic [31:0] crc32_byte(input logic [31:0] c, input logic [7:0] b);
        logic [31:0] x;
        x = c ^ {24'b0, b};
        for (int i = 0; i < 8; i++)
            x = x[0] ? ((x >> 1) ^ 32'hEDB8_8320) : (x >> 1);
        return x;
    endfunction

    typedef enum logic [3:0] {
        RX_CMD,
        RX_ADDR,
        RX_LEN,
        RX_DATA,
        RX_CRC,
        CHECK,        // Register frame_ok (timing: see frame_ok_q below).
        EXEC,
        RUN_WAIT,     // 0x05: wait for the core to finish (UART silent).
        AXI_WR,       // Write in flight (AW/W).
        AXI_WR_RESP,  // Wait B.
        AXI_RD,       // Read in flight (AR).
        AXI_RD_RESP,  // Wait R.
        TX_PREP,      // Select next response byte.
        TX_SEND,      // Hand byte to uart_tx.
        TX_HOLD       // Wait for uart_tx to accept.
    } state_t;

    typedef enum logic [1:0] { PH_HDR, PH_DATA, PH_CRC } phase_t;

    state_t      state;
    phase_t      phase;

    logic [7:0]  cmd;
    logic [31:0] addr;
    logic [31:0] len;
    logic [31:0] cnt;             // Byte counter (field / payload / loop).
    logic [31:0] crc;             // Running CRC (RX, then TX).
    logic [31:0] crc_rx;          // Received CRC.
    logic [7:0]  payload [0:MAX_WRITE-1];
    logic [31:0] reg_val;         // 0x01 read value / 0x02 write value.
    logic [31:0] resp_len;
    logic        resp_err;
    logic [31:0] timeout;
    logic [7:0]  txb;
    logic        axi_err;
    logic [31:0] data_off;        // Current buffer byte offset (0x03/0x04/0x05).

    // 0x05 Run: arguments (the last 8 payload bytes: out offset, out count,
    // shifted in as they arrive), the status block read back after the
    // operation, and the index of the status word being read.
    logic [63:0] run_args;
    logic [31:0] run_off, run_cnt;
    logic [31:0] run_st, run_cyc, run_err;
    logic [95:0] run_blk;
    logic [1:0]  rd_seq;
    logic        run_ok;
    assign run_off = run_args[31:0];
    assign run_cnt = run_args[63:32];
    assign run_blk = {run_err, run_cyc, run_st};
    assign run_ok  = run_st[1] && !run_st[2] && !run_st[0];  // done, no error, idle.

    (* ASYNC_REG = "TRUE" *) logic [1:0] busy_sync;
    logic        busy_s;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) busy_sync <= '0;
        else        busy_sync <= {busy_sync[0], core_busy};
    end
    assign busy_s = busy_sync[1];

    // Command validation (evaluated once the whole frame is in).
    //
    // frame_ok is a deep cone (DATA_SIZE - addr subtract, 32-bit length
    // compares, CRC compare, per-command mux). Using it directly in EXEC put
    // it in front of the clock enables of every m_axi_* register: the worst
    // sys_clk path of the whole design (15 logic levels, ~0.02 ns slack at
    // 125 MHz on the Arty Z7, ~0.01 ns at 100 MHz on the Arty A7). The CHECK
    // state registers it into frame_ok_q one cycle earlier instead; EXEC only
    // reads the register. Inputs (cmd, addr, len, payload, crc, crc_rx) are
    // all final once RX_CRC has taken its last byte and none change in
    // CHECK, so frame_ok_q equals what EXEC used to compute. Cost: one
    // sys_clk cycle per command; the core's own cycle counts are unaffected.
    logic [31:0] rd_count;
    logic        frame_ok;
    logic        frame_ok_q;
    assign rd_count = {payload[3], payload[2], payload[1], payload[0]};
    always_comb begin
        frame_ok = (~crc == crc_rx);
        case (cmd)
            CMD_READ_REG:   frame_ok = frame_ok && (len == 32'd0);
            CMD_WRITE_REG:  frame_ok = frame_ok && (len == 32'd4);
            CMD_WRITE_DATA: frame_ok = frame_ok && (len != 32'd0) && (len <= MAX_WRITE) &&
                                       (addr < DATA_SIZE) && (len <= DATA_SIZE - addr);
            CMD_READ_DATA:  frame_ok = frame_ok && (len == 32'd4) && (rd_count != 32'd0) &&
                                       (addr < DATA_SIZE) && (rd_count <= DATA_SIZE - addr);
            CMD_RUN:        frame_ok = frame_ok && (len == 32'd12) &&
                                       ((run_cnt == 32'd0) ||
                                        ((run_off < DATA_SIZE) && (run_cnt <= DATA_SIZE - run_off)));
            default:        frame_ok = 1'b0;
        endcase
    end

    logic [15:0] data_bus_addr;
    assign data_bus_addr = DATA_BASE + {2'b00, data_off[13:2], 2'b00};

    assign m_axi_bready = 1'b1;
    assign m_axi_rready = 1'b1;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state         <= RX_CMD;
            phase         <= PH_HDR;
            cmd           <= '0;
            addr          <= '0;
            len           <= '0;
            cnt           <= '0;
            crc           <= 32'hFFFF_FFFF;
            crc_rx        <= '0;
            reg_val       <= '0;
            resp_len      <= '0;
            resp_err      <= 1'b0;
            frame_ok_q    <= 1'b0;
            timeout       <= '0;
            txb           <= '0;
            axi_err       <= 1'b0;
            data_off      <= '0;
            run_args      <= '0;
            run_st        <= '0;
            run_cyc       <= '0;
            run_err       <= '0;
            rd_seq        <= '0;
            tx_byte       <= '0;
            tx_valid      <= 1'b0;
            m_axi_awaddr  <= '0;
            m_axi_awvalid <= 1'b0;
            m_axi_wdata   <= '0;
            m_axi_wstrb   <= '0;
            m_axi_wvalid  <= 1'b0;
            m_axi_araddr  <= '0;
            m_axi_arvalid <= 1'b0;
        end else begin
            tx_valid <= 1'b0;

            // Inter-byte timeout while a frame is being received.
            // (Explicit compares rather than `inside`, for Icarus Verilog.)
            if (state == RX_ADDR || state == RX_LEN || state == RX_DATA || state == RX_CRC) begin
                if (rx_valid)
                    timeout <= '0;
                else if (timeout == TIMEOUT_CYCLES - 1) begin
                    timeout <= '0;
                    state   <= RX_CMD;
                end else
                    timeout <= timeout + 32'd1;
            end else begin
                timeout <= '0;
            end

            case (state)
                // ----- Receive command frame -----
                RX_CMD: if (rx_valid) begin
                    cmd   <= rx_byte;
                    crc   <= crc32_byte(32'hFFFF_FFFF, rx_byte);
                    cnt   <= '0;
                    state <= RX_ADDR;
                end

                RX_ADDR: if (rx_valid) begin
                    addr[8*cnt[1:0] +: 8] <= rx_byte;
                    crc <= crc32_byte(crc, rx_byte);
                    cnt <= cnt + 32'd1;
                    if (cnt[1:0] == 2'd3) begin
                        cnt   <= '0;
                        state <= RX_LEN;
                    end
                end

                RX_LEN: if (rx_valid) begin
                    len[8*cnt[1:0] +: 8] <= rx_byte;
                    crc <= crc32_byte(crc, rx_byte);
                    cnt <= cnt + 32'd1;
                    if (cnt[1:0] == 2'd3) begin
                        cnt   <= '0;
                        if ({rx_byte, len[23:0]} == 32'd0)
                            state <= RX_CRC;
                        else
                            state <= RX_DATA;
                    end
                end

                RX_DATA: if (rx_valid) begin
                    if (cnt < MAX_WRITE)
                        payload[cnt[$clog2(MAX_WRITE)-1:0]] <= rx_byte;
                    run_args <= {rx_byte, run_args[63:8]};
                    crc <= crc32_byte(crc, rx_byte);
                    cnt <= cnt + 32'd1;
                    if (cnt == len - 32'd1) begin
                        cnt   <= '0;
                        state <= RX_CRC;
                    end
                end

                RX_CRC: if (rx_valid) begin
                    crc_rx[8*cnt[1:0] +: 8] <= rx_byte;
                    cnt <= cnt + 32'd1;
                    if (cnt[1:0] == 2'd3)
                        state <= CHECK;
                end

                // ----- Execute -----
                CHECK: begin
                    frame_ok_q <= frame_ok;
                    state      <= EXEC;
                end

                EXEC: begin
                    cnt      <= '0;
                    axi_err  <= 1'b0;
                    data_off <= addr;
                    reg_val  <= rd_count;
                    resp_err <= !frame_ok_q;
                    resp_len <= '0;
                    if (!frame_ok_q) begin
                        state <= TX_PREP;
                    end else begin
                        case (cmd)
                            CMD_READ_REG: begin
                                m_axi_araddr  <= addr[15:0];
                                m_axi_arvalid <= 1'b1;
                                state         <= AXI_RD;
                            end
                            CMD_WRITE_REG, CMD_RUN: begin
                                m_axi_awaddr  <= addr[15:0];
                                m_axi_awvalid <= 1'b1;
                                m_axi_wdata   <= rd_count;
                                m_axi_wstrb   <= 4'hF;
                                m_axi_wvalid  <= 1'b1;
                                state         <= AXI_WR;
                            end
                            CMD_WRITE_DATA: begin
                                state <= AXI_WR;
                                m_axi_awaddr  <= DATA_BASE + {2'b00, addr[13:2], 2'b00};
                                m_axi_awvalid <= 1'b1;
                                m_axi_wdata   <= {4{payload[0]}};
                                m_axi_wstrb   <= 4'b0001 << addr[1:0];
                                m_axi_wvalid  <= 1'b1;
                            end
                            default: begin   // CMD_READ_DATA: stream bytes.
                                resp_len <= rd_count;
                                state    <= TX_PREP;
                            end
                        endcase
                    end
                    crc   <= 32'hFFFF_FFFF;
                    phase <= PH_HDR;
                end

                AXI_WR: begin
                    if (m_axi_awvalid && m_axi_awready) m_axi_awvalid <= 1'b0;
                    if (m_axi_wvalid && m_axi_wready)   m_axi_wvalid  <= 1'b0;
                    if ((!m_axi_awvalid || m_axi_awready) && (!m_axi_wvalid || m_axi_wready))
                        state <= AXI_WR_RESP;
                end

                AXI_WR_RESP: if (m_axi_bvalid) begin
                    if (m_axi_bresp != 2'b00) axi_err <= 1'b1;
                    if (cmd == CMD_WRITE_DATA && cnt != len - 32'd1) begin
                        // Next payload byte.
                        cnt           <= cnt + 32'd1;
                        data_off      <= data_off + 32'd1;
                        m_axi_awaddr  <= DATA_BASE + {2'b00, 12'(( data_off + 32'd1) >> 2), 2'b00};
                        m_axi_awvalid <= 1'b1;
                        m_axi_wdata   <= {4{payload[8'(cnt + 32'd1)]}};
                        m_axi_wstrb   <= 4'b0001 << 2'(data_off + 32'd1);
                        m_axi_wvalid  <= 1'b1;
                        state         <= AXI_WR;
                    end else if (cmd == CMD_RUN && m_axi_bresp == 2'b00 && !axi_err) begin
                        cnt   <= '0;              // Counts the wait.
                        state <= RUN_WAIT;
                    end else begin
                        resp_err <= (m_axi_bresp != 2'b00) || axi_err;
                        cnt      <= '0;
                        state    <= TX_PREP;
                    end
                end

                // 0x05: the core is running; nothing is sent or received.
                RUN_WAIT: begin
                    cnt <= cnt + 32'd1;
                    if ((cnt >= RUN_GUARD && !busy_s) || cnt == RUN_TIMEOUT_CYCLES - 1) begin
                        cnt           <= '0;
                        rd_seq        <= 2'd0;
                        m_axi_araddr  <= REG_STATUS;
                        m_axi_arvalid <= 1'b1;
                        state         <= AXI_RD;
                    end
                end

                AXI_RD: begin
                    if (m_axi_arready) begin
                        m_axi_arvalid <= 1'b0;
                        state         <= AXI_RD_RESP;
                    end
                end

                AXI_RD_RESP: if (m_axi_rvalid) begin
                    if (cmd == CMD_READ_REG) begin
                        reg_val  <= m_axi_rdata;
                        resp_len <= 32'd4;
                        resp_err <= (m_axi_rresp != 2'b00);
                        state    <= TX_PREP;
                    end else if (cmd == CMD_RUN && phase == PH_HDR) begin
                        // Status block: STATUS, CYCLE_COUNT, ERROR_CODE.
                        if (m_axi_rresp != 2'b00) axi_err <= 1'b1;
                        case (rd_seq)
                            2'd0:    run_st  <= m_axi_rdata;
                            2'd1:    run_cyc <= m_axi_rdata;
                            default: run_err <= m_axi_rdata;
                        endcase
                        if (rd_seq == 2'd2) begin
                            resp_err <= axi_err || (m_axi_rresp != 2'b00);
                            resp_len <= 32'd12 + (run_ok ? run_cnt : 32'd0);
                            data_off <= run_off;
                            cnt      <= '0;
                            state    <= TX_PREP;
                        end else begin
                            rd_seq        <= rd_seq + 2'd1;
                            m_axi_araddr  <= (rd_seq == 2'd0) ? REG_CYCLE_COUNT : REG_ERROR_CODE;
                            m_axi_arvalid <= 1'b1;
                            state         <= AXI_RD;
                        end
                    end else begin
                        txb   <= m_axi_rdata[8*data_off[1:0] +: 8];
                        state <= TX_SEND;
                    end
                end

                // ----- Transmit response -----
                TX_PREP: begin
                    case (phase)
                        PH_HDR: begin
                            case (cnt[2:0])
                                3'd0:    txb <= {7'b0, resp_err};
                                3'd1:    txb <= resp_err ? 8'h00 : resp_len[7:0];
                                3'd2:    txb <= resp_err ? 8'h00 : resp_len[15:8];
                                3'd3:    txb <= resp_err ? 8'h00 : resp_len[23:16];
                                default: txb <= resp_err ? 8'h00 : resp_len[31:24];
                            endcase
                            state <= TX_SEND;
                        end
                        PH_DATA: begin
                            if (cmd == CMD_READ_DATA || (cmd == CMD_RUN && cnt >= 32'd12)) begin
                                m_axi_araddr  <= data_bus_addr;
                                m_axi_arvalid <= 1'b1;
                                state         <= AXI_RD;
                            end else if (cmd == CMD_RUN) begin
                                txb   <= run_blk[8*cnt[3:0] +: 8];
                                state <= TX_SEND;
                            end else begin
                                txb   <= reg_val[8*cnt[1:0] +: 8];
                                state <= TX_SEND;
                            end
                        end
                        default: begin  // PH_CRC
                            txb   <= ~crc[8*cnt[1:0] +: 8];
                            state <= TX_SEND;
                        end
                    endcase
                end

                TX_SEND: if (tx_ready) begin
                    tx_byte  <= txb;
                    tx_valid <= 1'b1;
                    if (phase != PH_CRC)
                        crc <= crc32_byte(crc, txb);
                    state <= TX_HOLD;
                end

                TX_HOLD: if (!tx_ready) begin
                    // uart_tx has taken the byte; advance.
                    state <= TX_PREP;
                    cnt   <= cnt + 32'd1;
                    case (phase)
                        PH_HDR: if (cnt == 32'd4) begin
                            cnt   <= '0;
                            if (resp_err || resp_len == 32'd0)
                                phase <= PH_CRC;
                            else
                                phase <= PH_DATA;
                        end
                        PH_DATA: begin
                            if (cmd != CMD_RUN || cnt >= 32'd12)
                                data_off <= data_off + 32'd1;
                            if (cnt == resp_len - 32'd1) begin
                                cnt   <= '0;
                                phase <= PH_CRC;
                            end
                        end
                        default: if (cnt == 32'd3) begin
                            cnt   <= '0;
                            state <= RX_CMD;
                        end
                    endcase
                end

                default: state <= RX_CMD;
            endcase
        end
    end

endmodule
