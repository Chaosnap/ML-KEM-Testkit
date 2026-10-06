set root /home/vivadouser/project/ML_KEM_Test/ML-KEM-Testkit
set out $root/reports/core206_debug2/paper_metrics
file mkdir $out
open_checkpoint $root/reports/core206_debug2/implemented.dcp
set core_cells [get_cells -hierarchical -filter {NAME =~ u_mlkem/* && IS_PRIMITIVE == 1}]
set core_slices [get_sites -of_objects $core_cells -filter {SITE_TYPE =~ SLICE*}]
set core_tiles [get_tiles -of_objects $core_slices]
set f [open $out/physical_metrics.txt w]
puts $f "part [get_property PART [current_design]]"
puts $f "core_slice_sites [llength [lsort -unique $core_slices]]"
puts $f "core_clb_tiles [llength [lsort -unique $core_tiles]]"
set clk [get_clocks -of_objects [get_pins u_mmcm/CLKOUT0]]
set cp [get_property PERIOD $clk]
set worst [get_timing_paths -group $clk -setup -max_paths 1]
set slack [get_property SLACK $worst]
puts $f "core_period_ns $cp"
puts $f "core_wns_ns $slack"
puts $f "critical_path_requirement_ns [get_property REQUIREMENT $worst]"
puts $f "sta_fmax_estimate_mhz [expr {1000.0 / ($cp - $slack)}]"
puts $f "core_whs_ns [get_property SLACK [get_timing_paths -group $clk -hold -max_paths 1]]"
close $f
set f [open $out/core_slice_sites.txt w]; puts $f [join [lsort -unique $core_slices] \n]; close $f
set f [open $out/core_clb_tiles.txt w]; puts $f [join [lsort -unique $core_tiles] \n]; close $f
report_power -file $out/power_vectorless.rpt
report_timing -group $clk -max_paths 10 -file $out/core_timing.rpt
puts "PAPER_METRICS_EXTRACTION_COMPLETE"
exit
