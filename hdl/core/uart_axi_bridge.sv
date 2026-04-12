// uart_axi_bridge.sv - UART-to-AXI-Lite protocol bridge
//
// Translates the pqc-testkit UART protocol into AXI-Lite read/write
// transactions. This bridges the gap between a serial connection
// (used on dev boards like Arty A7) and the AXI-Lite CSR interface
// of the PQC accelerator core.
//
// Protocol (matches pkg/fpga/uart/serial.go):
//
// Host -> FPGA command frame:
//   [CMD:1][ADDR:4][LEN:4][DATA:LEN][CRC:4]
//
//   CMD values:
//     0x01: Read register  (ADDR=reg offset, LEN=0, returns 4 bytes)
//     0x02: Write register (ADDR=reg offset, LEN=4, DATA=value)
//     0x03: Write data     (ADDR=buf offset, LEN=data length, DATA=payload)
//     0x04: Read data      (ADDR=buf offset, LEN=4, DATA=requested length)
//
// FPGA -> Host response frame:
//   [STATUS:1][LEN:4][DATA:LEN][CRC:4]
//
//   STATUS values:
//     0x00: OK
//     0x01: Error

module uart_axi_bridge (
    input  logic        clk,
    input  logic        rst_n,

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

    // =========================================================================
    // Frame receive state machine
    // =========================================================================

    typedef enum logic [3:0] {
        RX_CMD,
        RX_ADDR,
        RX_LEN,
        RX_DATA,
        RX_CRC,
        EXEC_READ,
        EXEC_WRITE,
        TX_RESP,
        TX_DONE
    } rx_state_t;

    rx_state_t rx_state;

    logic [7:0]  cmd_reg;
    logic [31:0] addr_reg;
    logic [31:0] len_reg;
    logic [7:0]  data_buf [0:255];  // Max 256 byte payload.
    logic [31:0] crc_reg;
    logic [7:0]  byte_cnt;          // Byte counter within current field.

    // Response buffer.
    logic [7:0]  resp_buf [0:263];   // STATUS + LEN + DATA + CRC.
    logic [15:0] resp_len;
    logic [15:0] resp_idx;

    // AXI transaction state.
    logic        axi_read_pending;
    logic        axi_write_pending;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_state          <= RX_CMD;
            cmd_reg           <= '0;
            addr_reg          <= '0;
            len_reg           <= '0;
            crc_reg           <= '0;
            byte_cnt          <= '0;
            resp_len          <= '0;
            resp_idx          <= '0;
            tx_valid          <= 1'b0;
            tx_byte           <= '0;
            m_axi_awvalid     <= 1'b0;
            m_axi_wvalid      <= 1'b0;
            m_axi_arvalid     <= 1'b0;
            m_axi_bready      <= 1'b1;
            m_axi_rready      <= 1'b1;
            m_axi_wstrb       <= 4'hF;
            axi_read_pending  <= 1'b0;
            axi_write_pending <= 1'b0;
        end else begin
            tx_valid <= 1'b0;

            // Clear AXI handshakes.
            if (m_axi_awvalid && m_axi_awready) m_axi_awvalid <= 1'b0;
            if (m_axi_wvalid && m_axi_wready)   m_axi_wvalid  <= 1'b0;
            if (m_axi_arvalid && m_axi_arready)  m_axi_arvalid <= 1'b0;

            case (rx_state)
                // ----- Receive command frame -----
                RX_CMD: begin
                    if (rx_valid) begin
                        cmd_reg  <= rx_byte;
                        byte_cnt <= '0;
                        rx_state <= RX_ADDR;
                    end
                end

                RX_ADDR: begin
                    if (rx_valid) begin
                        // Little-endian: first byte is LSB (matches Go binary.LittleEndian).
                        case (byte_cnt[1:0])
                            2'd0: addr_reg[7:0]   <= rx_byte;
                            2'd1: addr_reg[15:8]  <= rx_byte;
                            2'd2: addr_reg[23:16] <= rx_byte;
                            2'd3: addr_reg[31:24] <= rx_byte;
                        endcase
                        byte_cnt <= byte_cnt + 1;
                        if (byte_cnt == 3)
                            rx_state <= RX_LEN;
                    end
                end

                RX_LEN: begin
                    if (rx_valid) begin
                        // Little-endian length field.
                        case (byte_cnt[1:0])
                            2'd0: len_reg[7:0]   <= rx_byte;
                            2'd1: len_reg[15:8]  <= rx_byte;
                            2'd2: len_reg[23:16] <= rx_byte;
                            2'd3: len_reg[31:24] <= rx_byte;
                        endcase
                        byte_cnt <= byte_cnt + 1;
                        if (byte_cnt == 7) begin
                            byte_cnt <= '0;
                            if (len_reg[23:0] == 0 && rx_byte == 0)
                                rx_state <= RX_CRC;  // No data payload.
                            else
                                rx_state <= RX_DATA;
                        end
                    end
                end

                RX_DATA: begin
                    if (rx_valid) begin
                        data_buf[byte_cnt] <= rx_byte;
                        byte_cnt <= byte_cnt + 1;
                        if ({24'b0, byte_cnt} + 1 >= len_reg) begin
                            byte_cnt <= '0;
                            rx_state <= RX_CRC;
                        end
                    end
                end

                RX_CRC: begin
                    if (rx_valid) begin
                        crc_reg  <= {crc_reg[23:0], rx_byte};
                        byte_cnt <= byte_cnt + 1;
                        if (byte_cnt == 3) begin
                            // CRC check skipped for simplicity in initial version.
                            // Execute the command.
                            case (cmd_reg)
                                8'h01: rx_state <= EXEC_READ;   // Read register.
                                8'h02: rx_state <= EXEC_WRITE;  // Write register.
                                8'h03: rx_state <= EXEC_WRITE;  // Write data.
                                8'h04: rx_state <= EXEC_READ;   // Read data.
                                default: begin
                                    // Unknown command: send error response.
                                    resp_buf[0] <= 8'h01;  // Error status.
                                    resp_buf[1] <= '0;
                                    resp_buf[2] <= '0;
                                    resp_buf[3] <= '0;
                                    resp_buf[4] <= '0;
                                    resp_len    <= 9;  // STATUS + LEN + CRC.
                                    resp_idx    <= '0;
                                    rx_state    <= TX_RESP;
                                end
                            endcase
                        end
                    end
                end

                // ----- Execute AXI transactions -----
                EXEC_READ: begin
                    if (!axi_read_pending) begin
                        m_axi_araddr   <= addr_reg[15:0];
                        m_axi_arvalid  <= 1'b1;
                        axi_read_pending <= 1'b1;
                    end else if (m_axi_rvalid) begin
                        // Build response: STATUS(OK) + LEN(4, little-endian) + DATA(4 bytes, little-endian) + CRC(4).
                        // Go reads with binary.LittleEndian, so send LSB first.
                        resp_buf[0] <= 8'h00;  // OK status.
                        resp_buf[1] <= 8'h04;  // LEN LSB = 4.
                        resp_buf[2] <= 8'h00;  // LEN.
                        resp_buf[3] <= 8'h00;  // LEN.
                        resp_buf[4] <= 8'h00;  // LEN MSB.
                        resp_buf[5] <= m_axi_rdata[7:0];    // DATA LSB.
                        resp_buf[6] <= m_axi_rdata[15:8];
                        resp_buf[7] <= m_axi_rdata[23:16];
                        resp_buf[8] <= m_axi_rdata[31:24];  // DATA MSB.
                        // CRC placeholder (4 bytes of zeros).
                        resp_buf[9]  <= '0;
                        resp_buf[10] <= '0;
                        resp_buf[11] <= '0;
                        resp_buf[12] <= '0;
                        resp_len <= 13;
                        resp_idx <= '0;
                        axi_read_pending <= 1'b0;
                        rx_state <= TX_RESP;
                    end
                end

                EXEC_WRITE: begin
                    if (!axi_write_pending) begin
                        m_axi_awaddr  <= addr_reg[15:0];
                        m_axi_awvalid <= 1'b1;
                        // Data is already little-endian from Go: buf[0]=LSB, buf[3]=MSB.
                        m_axi_wdata   <= {data_buf[3], data_buf[2], data_buf[1], data_buf[0]};
                        m_axi_wvalid  <= 1'b1;
                        axi_write_pending <= 1'b1;
                    end else if (m_axi_bvalid) begin
                        // Build response: STATUS(OK) + LEN(0, little-endian) + CRC.
                        resp_buf[0] <= 8'h00;  // OK status.
                        resp_buf[1] <= '0;     // LEN = 0 (LE).
                        resp_buf[2] <= '0;
                        resp_buf[3] <= '0;
                        resp_buf[4] <= '0;
                        // CRC placeholder.
                        resp_buf[5] <= '0;
                        resp_buf[6] <= '0;
                        resp_buf[7] <= '0;
                        resp_buf[8] <= '0;
                        resp_len <= 9;
                        resp_idx <= '0;
                        axi_write_pending <= 1'b0;
                        rx_state <= TX_RESP;
                    end
                end

                // ----- Transmit response -----
                TX_RESP: begin
                    if (tx_ready && resp_idx < resp_len) begin
                        tx_byte  <= resp_buf[resp_idx];
                        tx_valid <= 1'b1;
                        resp_idx <= resp_idx + 1;
                    end else if (resp_idx >= resp_len) begin
                        rx_state <= TX_DONE;
                    end
                end

                TX_DONE: begin
                    byte_cnt <= '0;
                    rx_state <= RX_CMD;  // Ready for next command.
                end

                default: rx_state <= RX_CMD;
            endcase
        end
    end

endmodule
