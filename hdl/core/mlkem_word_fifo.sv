// mlkem_word_fifo.sv - Six live words in an eight-word banked LUT RAM.
//
// The sequencer can append one word and remove zero, one or two words in
// the same cycle. Even/odd banks each need only one asynchronous read port:
// consecutive head words always reside in different banks. No payload reset
// or shifts: only the pointers/count are reset. Logical capacity stays six,
// matching the controller's reservations for three outstanding BRAM reads.
module mlkem_word_fifo (
    input  logic        clk,
    input  logic        clr,
    input  logic        push,
    input  logic [31:0] din,
    input  logic [1:0]  pop_count,
    output logic [31:0] head0,
    output logic [31:0] head1,
    output logic [2:0]  count
);
    (* ram_style = "distributed" *) logic [31:0] even_mem [0:3];
    (* ram_style = "distributed" *) logic [31:0] odd_mem [0:3];
    logic [2:0] rd_ptr, wr_ptr;
    logic [1:0] even_addr;
    logic [31:0] even_head, odd_head;
    assign even_addr = rd_ptr[2:1] + {1'b0, rd_ptr[0]};
    assign even_head = even_mem[even_addr];
    assign odd_head = odd_mem[rd_ptr[2:1]];
    assign head0 = rd_ptr[0] ? odd_head : even_head;
    assign head1 = rd_ptr[0] ? even_head : odd_head;

    always_ff @(posedge clk) begin
        if (!clr && push) begin
            if (wr_ptr[0]) odd_mem[wr_ptr[2:1]] <= din;
            else even_mem[wr_ptr[2:1]] <= din;
        end
    end
    always_ff @(posedge clk) begin
        if (clr) begin
            rd_ptr <= '0;
            wr_ptr <= '0;
            count <= '0;
        end else begin
            rd_ptr <= rd_ptr + {1'b0, pop_count};
            if (push) wr_ptr <= wr_ptr + 3'd1;
            count <= count - {1'b0, pop_count} + {2'b0, push};
        end
    end
endmodule
