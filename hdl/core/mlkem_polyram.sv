// mlkem_polyram.sv - Polynomial coefficient RAM for the ML-KEM core
//
// True dual-port block RAM holding 16 polynomial slots. Each 24-bit word
// packs two consecutive coefficients: word {slot, w} = {f[2w+1], f[2w]}.
// Read latency is one cycle on both ports.

module mlkem_polyram (
    input  logic        clk,

    input  logic        a_en,
    input  logic        a_we,
    input  logic [10:0] a_addr,
    input  logic [23:0] a_din,
    output logic [23:0] a_dout,

    input  logic        b_en,
    input  logic        b_we,
    input  logic [10:0] b_addr,
    input  logic [23:0] b_din,
    output logic [23:0] b_dout
);

    logic [23:0] mem [0:2047];

    always_ff @(posedge clk) begin
        if (a_en) begin
            if (a_we)
                mem[a_addr] <= a_din;
            a_dout <= mem[a_addr];
        end
    end

    always_ff @(posedge clk) begin
        if (b_en) begin
            if (b_we)
                mem[b_addr] <= b_din;
            b_dout <= mem[b_addr];
        end
    end

endmodule
