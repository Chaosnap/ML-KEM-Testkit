// mlkem_polyram.sv - Polynomial coefficient RAM for the ML-KEM core
//
// True dual-port block RAM holding 8 polynomial slots (ML-KEM-768 needs 8). Each 24-bit word
// packs two consecutive coefficients: word {slot, w} = {f[2w+1], f[2w]}.
// Read latency is two cycles on both ports (output registers, DOA/DOB_REG).

module mlkem_polyram (
    input  logic        clk,

    input  logic        a_en,
    input  logic        a_we,
    input  logic [9:0]  a_addr,
    input  logic [23:0] a_din,
    output logic [23:0] a_dout,

    input  logic        b_en,
    input  logic        b_we,
    input  logic [9:0]  b_addr,
    input  logic [23:0] b_din,
    output logic [23:0] b_dout
);

    logic [23:0] mem [0:1023];
    logic [23:0] a_q, b_q;

    always_ff @(posedge clk) begin
        if (a_en) begin
            if (a_we)
                mem[a_addr] <= a_din;
            a_q <= mem[a_addr];
        end
        a_dout <= a_q;
    end

    always_ff @(posedge clk) begin
        if (b_en) begin
            if (b_we)
                mem[b_addr] <= b_din;
            b_q <= mem[b_addr];
        end
        b_dout <= b_q;
    end

endmodule
