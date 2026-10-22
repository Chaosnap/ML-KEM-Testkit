// SPDX-License-Identifier: MIT
// Native sakura-x-shell inter-FPGA handshake -> existing ML-KEM AXI-Lite.
// Compatible with the pinned Spartan controller's command_control.v.
// One outstanding transaction; a write is acknowledged ONLY after AXI B.
// Reading START (0x100) launches CTRL.start and delays the read response until
// completion + a quiet tail. No CSR/status polling takes place while busy.
module sakura_shell_bridge #(
    parameter int BUS_HZ = 50_000_000,
    parameter int PRE_CYCLES = BUS_HZ / 500,       // 2 ms before trigger
    parameter int DEFAULT_TAIL = BUS_HZ / 50,     // 20 ms after busy falls
    parameter int RUN_TIMEOUT = BUS_HZ           // 1 second watchdog
) (
    input logic clk, rst_n,
    input logic i_addr_valid, i_write_enable, i_write_data_valid, i_read_data_ready,
    input logic [31:0] i_common,
    output logic [31:0] o_read_data,
    output logic o_addr_ready, o_read_data_valid, o_write_data_ready,
    input logic core_busy, core_error,
    output logic run_active,
    output logic [15:0] m_axi_awaddr, m_axi_araddr,
    output logic [31:0] m_axi_wdata,
    output logic [3:0] m_axi_wstrb,
    output logic m_axi_awvalid, m_axi_wvalid, m_axi_bready,
    output logic m_axi_arvalid, m_axi_rready,
    input logic m_axi_awready, m_axi_wready, m_axi_bvalid,
    input logic m_axi_arready, m_axi_rvalid,
    input logic [1:0] m_axi_bresp, m_axi_rresp,
    input logic [31:0] m_axi_rdata
);
    localparam [31:0] START = 32'h100, IDENT = 32'h104,
                     TAIL = 32'h108, CLOCK_HZ = 32'h10c, FAULT = 32'h110,
                     INPUT_READY = 32'h114, RESULT_READY = 32'h118;
    localparam [31:0] MAGIC = 32'h534b0001; // SK, ABI 1
    typedef enum logic [4:0] {
        IDLE, ADDR_ACK, WRITE_DATA, WRITE_AXI, WRITE_RESP, WRITE_ACK, WRITE_DROP,
        READ_AXI, READ_RESP, READ_REPLY, READ_DROP,
        PRE_WAIT, START_SETTLE, WAIT_BUSY, WAIT_DONE, TAIL_WAIT
    } state_t;
    state_t state;
    logic [31:0] address, count, tail_cycles, fault;
    logic aw_sent, w_sent, write_request;
    logic [9:0] dk_words, ct_words;
    logic result_ready;
    (* ASYNC_REG = "TRUE" *) logic [1:0] busy_sync, error_sync;

    function automatic logic core_address(input logic [31:0] a);
        return a[1:0] == 0 && ((a <= 32'h2c) ||
                               (a >= 32'h4000 && a < 32'h6000));
    endfunction

    assign o_addr_ready = state == ADDR_ACK;
    assign o_write_data_ready = state == WRITE_ACK;
    assign o_read_data_valid = state == READ_REPLY;
    assign m_axi_awvalid = state == WRITE_AXI && !aw_sent;
    assign m_axi_wvalid = state == WRITE_AXI && !w_sent;
    assign m_axi_wstrb = 4'hf;
    assign m_axi_bready = state == WRITE_RESP;
    assign m_axi_arvalid = state == READ_AXI;
    assign m_axi_rready = state == READ_RESP;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy_sync <= 0;
            error_sync <= 0;
        end else begin
            busy_sync <= {busy_sync[0], core_busy};
            error_sync <= {error_sync[0], core_error};
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            address <= 0;
            write_request <= 0;
            count <= 0;
            tail_cycles <= DEFAULT_TAIL;
            fault <= 0;
            dk_words <= 0;
            ct_words <= 0;
            result_ready <= 0;
            aw_sent <= 0;
            w_sent <= 0;
            run_active <= 0;
            o_read_data <= 0;
            m_axi_awaddr <= 0;
            m_axi_araddr <= 0;
            m_axi_wdata <= 0;
        end else begin
            case (state)
                IDLE: if (i_addr_valid) begin
                    address <= i_common;
                    write_request <= i_write_enable;
                    state <= ADDR_ACK;
                end
                // S6 samples the opposite clock edge. Hold ACK until it drops
                // addr_valid; a combinational IDLE ack could vanish too early.
                ADDR_ACK: if (!i_addr_valid) begin
                    if (write_request) begin
                        state <= WRITE_DATA;
                    end else if (address == START) begin
                        run_active <= 1;
                        result_ready <= 0;
                        if (fault != 0 || busy_sync[1] || dk_words != 600 || ct_words != 272) begin
                            o_read_data <= 32'h80000000;
                            if (busy_sync[1]) fault <= 4;
                            else if (dk_words != 600 || ct_words != 272) fault <= 5;
                            state <= READ_REPLY;
                        end else begin
                            count <= PRE_CYCLES - 1;
                            ct_words <= 0; // each START needs a freshly loaded ciphertext
                            state <= PRE_WAIT;
                        end
                    end else if (address == IDENT) begin
                        o_read_data <= MAGIC;
                        state <= READ_REPLY;
                    end else if (address == TAIL) begin
                        o_read_data <= tail_cycles;
                        state <= READ_REPLY;
                    end else if (address == CLOCK_HZ) begin
                        o_read_data <= BUS_HZ;
                        state <= READ_REPLY;
                    end else if (address == RESULT_READY) begin
                        o_read_data <= {31'b0, result_ready};
                        state <= READ_REPLY;
                    end else if (address == INPUT_READY) begin
                        o_read_data <= {30'b0, (ct_words == 272), (dk_words == 600)};
                        state <= READ_REPLY;
                    end else if (address == FAULT) begin
                        o_read_data <= fault;
                        state <= READ_REPLY;
                    end else if (core_address(address)) begin
                        m_axi_araddr <= address[15:0];
                        state <= READ_AXI;
                    end else begin
                        fault <= 1;
                        o_read_data <= 32'hdeadbeef;
                        state <= READ_REPLY;
                    end
                end
                WRITE_DATA: if (i_write_data_valid) begin
                    if (address == TAIL) begin
                        // A nonzero quiet tail also protects short operations.
                        tail_cycles <= i_common < PRE_CYCLES ? PRE_CYCLES : i_common;
                        state <= WRITE_ACK;
                    end else if (core_address(address) &&
                                 !(address == 0 && i_common[0])) begin
                        m_axi_awaddr <= address[15:0];
                        m_axi_wdata <= i_common;
                        aw_sent <= 0;
                        w_sent <= 0;
                        if (address == 0 && i_common[1]) begin
                            fault <= 0;
                            dk_words <= 0;
                            ct_words <= 0;
                            result_ready <= 0;
                        end
                        state <= WRITE_AXI;
                    end else begin
                        fault <= 1; // malformed address / direct CTRL.start blocked
                        state <= WRITE_ACK;
                    end
                end
                WRITE_AXI: begin
                    if (m_axi_awvalid && m_axi_awready) aw_sent <= 1;
                    if (m_axi_wvalid && m_axi_wready) w_sent <= 1;
                    if ((aw_sent || m_axi_awready) && (w_sent || m_axi_wready))
                        state <= WRITE_RESP;
                end
                WRITE_RESP: if (m_axi_bvalid) begin
                    if (m_axi_bresp != 0) fault <= 2;
                    else if (!run_active) begin
                        // Sequential complete uploads only; incomplete/overwritten
                        // inputs cannot accidentally reuse a previous result.
                        if (address >= 32'h4000 && address < 32'h6000) result_ready <= 0;
                        if (address >= 32'h4000 && address < 32'h4960) begin
                            if (address == 32'h4000) dk_words <= 1;
                            else if (address == 32'h4000 + dk_words * 4)
                                dk_words <= dk_words + 1;
                            else dk_words <= 0;
                        end
                        if (address >= 32'h4960 && address < 32'h4da0) begin
                            if (address == 32'h4960) ct_words <= 1;
                            else if (address == 32'h4960 + ct_words * 4)
                                ct_words <= ct_words + 1;
                            else ct_words <= 0;
                            result_ready <= 0;
                        end
                    end
                    if (run_active) begin
                        if (m_axi_bresp != 0) begin
                            o_read_data <= 32'h80000000;
                            state <= READ_REPLY;
                        end else begin
                            // Let status synchronizers settle after the start write.
                            count <= 15;
                            state <= START_SETTLE;
                        end
                    end else state <= WRITE_ACK;
                end
                WRITE_ACK: state <= WRITE_DROP;
                WRITE_DROP: if (!i_write_data_valid) state <= IDLE;
                READ_AXI: if (m_axi_arready) state <= READ_RESP;
                READ_RESP: if (m_axi_rvalid) begin
                    o_read_data <= m_axi_rresp == 0 ? m_axi_rdata : 32'h80000000;
                    if (m_axi_rresp != 0) fault <= 2;
                    if (run_active) result_ready <= m_axi_rresp == 0 && m_axi_rdata == 2;
                    state <= READ_REPLY;
                end
                READ_REPLY: if (i_read_data_ready) begin
                    run_active <= 0;
                    state <= READ_DROP;
                end
                READ_DROP: if (!i_read_data_ready && !i_addr_valid) state <= IDLE;
                PRE_WAIT: if (count != 0) count <= count - 1;
                          else begin
                              m_axi_awaddr <= 0;
                              m_axi_wdata <= 1;
                              aw_sent <= 0;
                              w_sent <= 0;
                              state <= WRITE_AXI;
                          end
                START_SETTLE: if (count != 0) count <= count - 1;
                              else begin
                                  count <= RUN_TIMEOUT - 1;
                                  state <= WAIT_BUSY;
                              end
                WAIT_BUSY: begin
                    if (busy_sync[1]) state <= WAIT_DONE;
                    else if (error_sync[1]) begin
                        count <= tail_cycles - 1;
                        state <= TAIL_WAIT;
                    end else if (count == 0) begin
                        fault <= 3;
                        o_read_data <= 32'h80000000;
                        state <= READ_REPLY;
                    end else count <= count - 1;
                end
                WAIT_DONE: begin
                    if (!busy_sync[1]) begin
                        count <= tail_cycles - 1;
                        state <= TAIL_WAIT;
                    end else if (count == 0) begin
                        fault <= 3;
                        o_read_data <= 32'h80000000;
                        state <= READ_REPLY;
                    end else count <= count - 1;
                end
                TAIL_WAIT: if (count != 0) count <= count - 1;
                           else begin
                               m_axi_araddr <= 16'h0004; // one read, AFTER completion
                               state <= READ_AXI;
                           end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
