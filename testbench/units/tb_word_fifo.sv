`timescale 1ns/1ps
// Scoreboard uses a simple shifting queue, independently of the RTL banks.
module tb_word_fifo;
    logic clk=0, clr=1, push=0;
    always #5 clk=~clk;
    logic [31:0] din=0, head0, head1;
    logic [1:0] pop_count=0;
    logic [2:0] count;
    mlkem_word_fifo dut(.*);
    logic [31:0] refq[0:5];
    int used=0, pushed=0, pairs=0, both=0, full=0, clears=0;
    logic [31:0] rng=32'h63e026;
    task automatic check_heads;
        if(count !== 3'(used)) $fatal(1,"count=%0d expected=%0d",count,used);
        if(used>0 && head0 !== refq[0]) $fatal(1,"head0 mismatch");
        if(used>1 && head1 !== refq[1]) $fatal(1,"head1 mismatch");
    endtask
    initial begin
        repeat(3) @(negedge clk);
        for(int cycle=0;cycle<12000;cycle++) begin
            check_heads();
            rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};
            clr=(cycle%97==0);
            pop_count=2'(rng[7:0] % 3);
            if(int'(pop_count)>used) pop_count=2'(used);
            push=rng[8] && used-int'(pop_count)<6;
            din=rng ^ 32'(cycle*7919);
            @(posedge clk);
            if(clr) begin used=0; clears++; end
            else begin
                if(pop_count==2) pairs++;
                if(push && pop_count!=0) both++;
                for(int j=0;j<used-int'(pop_count);j++) refq[j]=refq[j+int'(pop_count)];
                used-=int'(pop_count);
                if(push) begin refq[used]=din; used++; pushed++; end
                if(used==6) full++;
            end
            @(negedge clk);
        end
        check_heads();
        if(pushed<1000 || pairs<100 || both<100 || full<1 || clears<100)
            $fatal(1,"insufficient FIFO coverage");
        $display("TB_WORD_FIFO PASS cycles=12000 push=%0d pop2=%0d simultaneous=%0d full=%0d clear=%0d",pushed,pairs,both,full,clears);
        $finish;
    end
endmodule
