####################################################################################
# Generated from file : 'pin_sasebo_giii_k7_orig_rev1_2.ucf' by AIST
####################################################################################

#================================================�@Timing constraint
# k_clk_osc_p<0>, k_clk_osc_n<0> : 200.000MHz
create_clock -period 5.000 -name i_osc_clk_p -waveform {0.000 2.500} [get_ports i_osc_clk_p]

# k_clk_osc_p<1>, k_clk_osc_n<1> : 200.000MHz
# create_clock -period 5.000 -name clk_osc1_p -waveform {0.000 2.500} [get_ports clk_osc1_p]

#==================== Pin assignment ===================
#################
# CLOCK / RESET #
#################
set_property PACKAGE_PIN AB2 [get_ports i_osc_clk_p]
set_property IOSTANDARD DIFF_HSTL_I [get_ports i_osc_clk_p]
set_property PACKAGE_PIN AC2 [get_ports i_osc_clk_n]
set_property IOSTANDARD DIFF_HSTL_I [get_ports i_osc_clk_n]

# Be careful with the following pins, they are used for Memory Interface Generator (MIG) of referece clock
# set_property PACKAGE_PIN AA3 [get_ports clk_osc1_p]
# set_property IOSTANDARD DIFF_HSTL_I [get_ports clk_osc1_p]
# set_property PACKAGE_PIN AA2 [get_ports clk_osc1_n]
# set_property IOSTANDARD DIFF_HSTL_I [get_ports clk_osc1_n]

# IO_0_16
set_property PACKAGE_PIN J8 [get_ports o_clk_osc_inh_n]
set_property IOSTANDARD LVCMOS25 [get_ports o_clk_osc_inh_n]

# IO_L14N_T2_SRCC_15
# set_property PACKAGE_PIN H18 [get_ports {k_clk_ext_n[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_clk_ext_n[0]}]
# IO_L14P_T2_SRCC_15
# set_property PACKAGE_PIN H17 [get_ports {k_clk_ext_p[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_clk_ext_p[0]}]
# IO_L11N_T1_SRCC_AD12N_15
# set_property PACKAGE_PIN F18 [get_ports {k_clk_ext_n[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_clk_ext_n[1]}]
# IO_L11P_T1_SRCC_AD12P_15
# set_property PACKAGE_PIN G17 [get_ports {k_clk_ext_p[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_clk_ext_p[1]}]

# IO_L6P_T0_13
# set_property PACKAGE_PIN R25 [get_ports k_reset_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_reset_b]

##########
# Switch #
##########
# IO_25_14
# set_property PACKAGE_PIN L23 [get_ports k_pushsw]
# set_property IOSTANDARD LVCMOS25 [get_ports k_pushsw]

# IO_L21P_T3_DQS_14
# set_property PACKAGE_PIN J21 [get_ports {k_dipsw[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[0]}]
# IO_L7P_T1_13
# set_property PACKAGE_PIN N19 [get_ports {k_dipsw[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[1]}]
# IO_25_15
# set_property PACKAGE_PIN M16 [get_ports {k_dipsw[2]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[2]}]
# IO_L7N_T1_13
# set_property PACKAGE_PIN M20 [get_ports {k_dipsw[3]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[3]}]
# IO_L24P_T3_RS1_15
# set_property PACKAGE_PIN L17 [get_ports {k_dipsw[4]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[4]}]
# IO_L4N_T0_13
# set_property PACKAGE_PIN N24 [get_ports {k_dipsw[5]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[5]}]
# IO_0_14
# set_property PACKAGE_PIN K21 [get_ports {k_dipsw[6]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[6]}]
# IO_L9P_T1_DQS_14
# set_property PACKAGE_PIN E21 [get_ports {k_dipsw[7]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_dipsw[7]}]

#######
# LED #
#######
# IO_L18N_T2_A23_15
set_property PACKAGE_PIN G20 [get_ports {o_led[0]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[0]}]
# IO_L21P_T3_DQS_15
set_property PACKAGE_PIN L19 [get_ports {o_led[1]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[1]}]
# IO_L24N_T3_RS0_15
 set_property PACKAGE_PIN K18 [get_ports {o_led[2]}]
 set_property IOSTANDARD LVCMOS25 [get_ports {o_led[2]}]
# IO_L18P_T2_A24_15
set_property PACKAGE_PIN H19 [get_ports {o_led[3]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[3]}]
# IO_0_15
set_property PACKAGE_PIN K15 [get_ports {o_led[4]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[4]}]
# IO_L20P_T3_13
set_property PACKAGE_PIN P16 [get_ports {o_led[5]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[5]}]
# IO_L19N_T3_VREF_13
set_property PACKAGE_PIN T19 [get_ports {o_led[6]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[6]}]
# IO_L19P_T3_13
set_property PACKAGE_PIN T18 [get_ports {o_led[7]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[7]}]
# IO_L6P_T0_16
set_property PACKAGE_PIN H12 [get_ports {o_led[8]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[8]}]
# IO_L6N_T0_VREF_16
set_property PACKAGE_PIN H11 [get_ports {o_led[9]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_led[9]}]

########
# GPIO #
########
# IO_L15P_T2_DQS_15
# set_property PACKAGE_PIN D19 [get_ports {k_header[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[0]}]
# IO_L20N_T3_13
# set_property PACKAGE_PIN N17 [get_ports {k_header[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[1]}]
# IO_0_13
# set_property PACKAGE_PIN N16 [get_ports {k_header[2]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[2]}]
# IO_L4P_T0_13
# set_property PACKAGE_PIN P24 [get_ports {k_header[3]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[3]}]
# IO_L12N_T1_MRCC_14
# set_property PACKAGE_PIN E23 [get_ports {k_header[4]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[4]}]
# IO_L12P_T1_MRCC_14
# set_property PACKAGE_PIN F22 [get_ports {k_header[5]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[5]}]
# IO_L13N_T2_MRCC_14
# set_property PACKAGE_PIN F23 [get_ports {k_header[6]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[6]}]
# IO_L13P_T2_MRCC_14
# set_property PACKAGE_PIN G22 [get_ports {k_header[7]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[7]}]
# IO_L14N_T2_SRCC_14
# set_property PACKAGE_PIN F24 [get_ports {k_header[8]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[8]}]
# IO_L14P_T2_SRCC_14
# set_property PACKAGE_PIN G24 [get_ports {k_header[9]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_header[9]}]

#############################################
# Spartan-6 HPIC (LVCMOS15, SSTL15 or HTSL) #
#############################################
# IO_L13N_T2_MRCC_33
# set_property PACKAGE_PIN AC11 [get_ports ks_hpic_clk_n]
# set_property IOSTANDARD DIFF_HSTL_I_DCI [get_ports ks_hpic_clk_n]
# IO_L13P_T2_MRCC_33 ks_hpic_clk_p
set_property PACKAGE_PIN AB11 [get_ports bus_clk ]
set_property IOSTANDARD LVCMOS15 [get_ports bus_clk ]
# 100 MHz 
create_clock -period 10.000 -name bus_clk -waveform {0.000 5.000} [get_ports bus_clk]

# IO_L6N_T0_VREF_34 ks_hpic_n[0]
set_property PACKAGE_PIN W4 [get_ports {i_bus_reset_n}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_bus_reset_n}]
# IO_L6P_T0_34 # ks_hpic_p[0]
set_property PACKAGE_PIN V4 [get_ports {i_common[0]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[0]}]
# IO_L8N_T1_34 # ks_hpic_n[1]
set_property PACKAGE_PIN V1 [get_ports {i_common[1]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[1]}]
# IO_L8P_T1_34 # ks_hpic_p[1]
set_property PACKAGE_PIN V2 [get_ports {i_common[2]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[2]}]
# IO_L10N_T1_34 # ks_hpic_n[2]
set_property PACKAGE_PIN Y1 [get_ports {i_common[3]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[3]}]
# IO_L10P_T1_34 # ks_hpic_p[2]
set_property PACKAGE_PIN W1 [get_ports {i_common[4]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[4]}]
# IO_L9N_T1_DQS_34 # ks_hpic_n[3]
set_property PACKAGE_PIN AC1 [get_ports {i_common[5]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[5]}]
# IO_L9P_T1_DQS_34 # ks_hpic_p[3]
set_property PACKAGE_PIN AB1 [get_ports {i_common[6]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[6]}]
# IO_L7N_T1_34 # ks_hpic_n[4]
set_property PACKAGE_PIN Y2 [get_ports {i_common[7]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[7]}]
# IO_L7P_T1_34 # ks_hpic_p[4]
set_property PACKAGE_PIN Y3 [get_ports {i_common[8]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[8]}]
# IO_L5N_T0_34 # ks_hpic_n[5]
set_property PACKAGE_PIN V6 [get_ports {i_common[9]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[9]}]
# IO_L5P_T0_34 # ks_hpic_p[5]
set_property PACKAGE_PIN U7 [get_ports {i_common[10]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[10]}]
# IO_L4N_T0_34 # ks_hpic_n[6]
set_property PACKAGE_PIN W3 [get_ports {i_common[11]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[11]}]
# IO_L4P_T0_34 # ks_hpic_p[6]
set_property PACKAGE_PIN V3 [get_ports {i_common[12]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[12]}]
# IO_L24N_T3_33 # ks_hpic_n[7]
set_property PACKAGE_PIN AF9 [get_ports {i_common[13]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[13]}]
# IO_L24P_T3_33 # ks_hpic_p[7]
set_property PACKAGE_PIN AF10 [get_ports {i_common[14]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[14]}]
# IO_L17N_T2_33 # ks_hpic_n[8]
set_property PACKAGE_PIN AD13 [get_ports {i_common[15]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[15]}]
# IO_L17P_T2_33 # ks_hpic_p[8]
set_property PACKAGE_PIN AC13 [get_ports {i_common[16]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[16]}]
# IO_L21N_T3_DQS_33 # ks_hpic_n[9]
set_property PACKAGE_PIN AF12 [get_ports {i_common[17]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[17]}]
# IO_L21P_T3_DQS_33 # ks_hpic_p[9]
set_property PACKAGE_PIN AE12 [get_ports {i_common[18]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[18]}]
# IO_L1N_T0_34 # ks_hpic_n[10]
set_property PACKAGE_PIN U5 [get_ports {i_common[19]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[19]}]
# IO_L1P_T0_34 # ks_hpic_p[10]
set_property PACKAGE_PIN U6 [get_ports {i_common[20]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[20]}]
# IO_L23N_T3_33 # ks_hpic_n[11]
set_property PACKAGE_PIN AF13 [get_ports {i_common[21]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[21]}]
# IO_L23P_T3_33 # ks_hpic_p[11]
set_property PACKAGE_PIN AE13 [get_ports {i_common[22]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[22]}]
# IO_L14N_T2_SRCC_33 # ks_hpic_n[12]
set_property PACKAGE_PIN AB10 [get_ports {i_common[23]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[23]}]
# IO_L14P_T2_SRCC_33 # ks_hpic_p[12]
set_property PACKAGE_PIN AA10 [get_ports {i_common[24]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[24]}]
# IO_L15N_T2_DQS_33 # ks_hpic_n[13]
set_property PACKAGE_PIN AC12 [get_ports {i_common[25]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[25]}]
# IO_L15P_T2_DQS_33 # ks_hpic_p[13]
set_property PACKAGE_PIN AB12 [get_ports {i_common[26]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[26]}]
# IO_L13N_T2_MRCC_34 # ks_hpic_n[14]
set_property PACKAGE_PIN AB4 [get_ports {i_common[27]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[27]}]
# IO_L13P_T2_MRCC_34 # ks_hpic_p[14]
set_property PACKAGE_PIN AA4 [get_ports {i_common[28]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[28]}]
# IO_L22N_T3_33 # ks_hpic_n[15]
set_property PACKAGE_PIN AF8 [get_ports {i_common[29]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[29]}]
# IO_L22P_T3_33 # ks_hpic_p[15]
set_property PACKAGE_PIN AE8 [get_ports {i_common[30]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[30]}]
# IO_L20N_T3_33 # ks_hpic_n[16]
set_property PACKAGE_PIN AE10 [get_ports {i_common[31]}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_common[31]}]
# IO_L20P_T3_33
set_property PACKAGE_PIN AD10 [get_ports {i_addr_valid}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_addr_valid}]
# IO_L18N_T2_33
set_property PACKAGE_PIN Y12 [get_ports {i_write_enable}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_write_enable}]
# IO_L18P_T2_33
set_property PACKAGE_PIN Y13 [get_ports {i_write_data_valid}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_write_data_valid}]
# IO_L16N_T2_33
set_property PACKAGE_PIN AA12 [get_ports {i_read_data_ready}]
set_property IOSTANDARD LVCMOS15 [get_ports {i_read_data_ready}]
# IO_L16P_T2_33
# set_property PACKAGE_PIN AA13 [get_ports {ks_hpic_p[18]}]
# set_property IOSTANDARD LVCMOS15 [get_ports {ks_hpic_p[18]}]

########################################
# Spartan-6 HRIC (LVCMOS25 or LVDS_25) #
########################################
# IO_L17N_T2_13 ks_hpic_n[0]
set_property PACKAGE_PIN T23 [get_ports {o_read_data[0]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[0]}]
# IO_L17P_T2_13 ks_hpic_p[0]
set_property PACKAGE_PIN T22 [get_ports {o_read_data[1]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[1]}]
# IO_L8N_T1_13
set_property PACKAGE_PIN L24 [get_ports {o_read_data[2]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[2]}]
# IO_L8P_T1_13
set_property PACKAGE_PIN M24 [get_ports {o_read_data[3]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[3]}]
# IO_L1N_T0_13
set_property PACKAGE_PIN K26 [get_ports {o_read_data[4]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[4]}]
# IO_L1P_T0_13
set_property PACKAGE_PIN K25 [get_ports {o_read_data[5]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[5]}]
# IO_L2N_T0_13
set_property PACKAGE_PIN P26 [get_ports {o_read_data[6]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[6]}]
# IO_L2P_T0_13
set_property PACKAGE_PIN R26 [get_ports {o_read_data[7]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[7]}]
# IO_L3N_T0_DQS_13
set_property PACKAGE_PIN L25 [get_ports {o_read_data[8]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[8]}]
# IO_L3P_T0_DQS_13
set_property PACKAGE_PIN M25 [get_ports {o_read_data[9]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[9]}]
# IO_L23N_T3_13
set_property PACKAGE_PIN T17 [get_ports {o_read_data[10]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[10]}]
# IO_L23P_T3_13
set_property PACKAGE_PIN U17 [get_ports {o_read_data[11]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[11]}]
# IO_L5N_T0_13
set_property PACKAGE_PIN M26 [get_ports {o_read_data[12]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[12]}]
# IO_L5P_T0_13
set_property PACKAGE_PIN N26 [get_ports {o_read_data[13]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[13]}]
# IO_L21N_T3_DQS_13
set_property PACKAGE_PIN R17 [get_ports {o_read_data[14]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[14]}]
# IO_L21P_T3_DQS_13
set_property PACKAGE_PIN R16 [get_ports {o_read_data[15]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[15]}]
# IO_L16N_T2_13
set_property PACKAGE_PIN R20 [get_ports {o_read_data[16]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[16]}]
# IO_L16P_T2_13
set_property PACKAGE_PIN T20 [get_ports {o_read_data[17]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[17]}]
# IO_L14N_T2_SRCC_13
set_property PACKAGE_PIN R23 [get_ports {o_read_data[18]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[18]}]
# IO_L14P_T2_SRCC_13
set_property PACKAGE_PIN R22 [get_ports {o_read_data[19]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[19]}]
# IO_L10N_T1_13
set_property PACKAGE_PIN M22 [get_ports {o_read_data[20]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[20]}]
# IO_L10P_T1_13
set_property PACKAGE_PIN M21 [get_ports {o_read_data[21]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[21]}]
# IO_L15N_T2_DQS_13
set_property PACKAGE_PIN T25 [get_ports {o_read_data[22]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[22]}]
# IO_L15P_T2_DQS_13
set_property PACKAGE_PIN T24 [get_ports {o_read_data[23]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[23]}]
# IO_L11N_T1_SRCC_13
set_property PACKAGE_PIN N23 [get_ports {o_read_data[24]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[24]}]
# IO_L11P_T1_SRCC_13
set_property PACKAGE_PIN P23 [get_ports {o_read_data[25]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[25]}]
# IO_L12N_T1_MRCC_13
set_property PACKAGE_PIN N22 [get_ports {o_read_data[26]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[26]}]
# IO_L12P_T1_MRCC_13
set_property PACKAGE_PIN N21 [get_ports {o_read_data[27]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[27]}]
# IO_L13N_T2_MRCC_13
set_property PACKAGE_PIN P21 [get_ports {o_read_data[28]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[28]}]
# IO_L13P_T2_MRCC_13
set_property PACKAGE_PIN R21 [get_ports {o_read_data[29]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[29]}]
# IO_L22N_T3_13
set_property PACKAGE_PIN M19 [get_ports {o_read_data[30]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[30]}]
# IO_L22P_T3_13
set_property PACKAGE_PIN N18 [get_ports {o_read_data[31]}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data[31]}]
# IO_L24N_T3_13
set_property PACKAGE_PIN P18 [get_ports {o_addr_ready}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_addr_ready}]
# IO_L24P_T3_13
set_property PACKAGE_PIN R18 [get_ports {o_write_data_ready}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_write_data_ready}]
# IO_L18N_T2_13
set_property PACKAGE_PIN U20 [get_ports {o_read_data_valid}]
set_property IOSTANDARD LVCMOS25 [get_ports {o_read_data_valid}]
# IO_L18P_T2_13
# set_property PACKAGE_PIN U19 [get_ports {ks_hric_p[17]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {ks_hric_p[17]}]
# IO_L9N_T1_DQS_13
# set_property PACKAGE_PIN P20 [get_ports {ks_hric_n[18]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {ks_hric_n[18]}]
# IO_L9P_T1_DQS_13
# set_property PACKAGE_PIN P19 [get_ports {ks_hric_p[18]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {ks_hric_p[18]}]

#############################
# FMC (LVDS_25 or LVCMOS25) #
#############################
# IO_L12N_T1_MRCC_AD5N_15
# set_property PACKAGE_PIN E17 [get_ports {k_fmc_clk_m2c_n[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_clk_m2c_n[0]}]
# IO_L12P_T1_MRCC_AD5P_15
# set_property PACKAGE_PIN F17 [get_ports {k_fmc_clk_m2c_p[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_clk_m2c_p[0]}]
# IO_L13N_T2_MRCC_15
# set_property PACKAGE_PIN D18 [get_ports {k_fmc_clk_m2c_n[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_clk_m2c_n[1]}]
# IO_L13P_T2_MRCC_15
# set_property PACKAGE_PIN E18 [get_ports {k_fmc_clk_m2c_p[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_clk_m2c_p[1]}]

# IO_L11N_T1_SRCC_16
# set_property PACKAGE_PIN F10 [get_ports {k_fmc_la_n[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[0]}]
# IO_L11P_T1_SRCC_16
# set_property PACKAGE_PIN G11 [get_ports {k_fmc_la_p[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[0]}]
# IO_L14N_T2_SRCC_16
# set_property PACKAGE_PIN D11 [get_ports {k_fmc_la_n[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[1]}]
# IO_L14P_T2_SRCC_16
# set_property PACKAGE_PIN E11 [get_ports {k_fmc_la_p[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[1]}]
# IO_L10N_T1_AD4N_15
# set_property PACKAGE_PIN E16 [get_ports {k_fmc_la_n[2]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[2]}]
# IO_L10P_T1_AD4P_15
# set_property PACKAGE_PIN E15 [get_ports {k_fmc_la_p[2]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[2]}]
# IO_L17N_T2_16
# set_property PACKAGE_PIN D13 [get_ports {k_fmc_la_n[3]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[3]}]
# IO_L17P_T2_16
# set_property PACKAGE_PIN D14 [get_ports {k_fmc_la_p[3]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[3]}]
# IO_L16N_T2_A27_15
# set_property PACKAGE_PIN F20 [get_ports {k_fmc_la_n[4]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[4]}]
# IO_L16P_T2_A28_15
# set_property PACKAGE_PIN G19 [get_ports {k_fmc_la_p[4]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[4]}]
# IO_L6N_T0_VREF_15
# set_property PACKAGE_PIN D16 [get_ports {k_fmc_la_n[5]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[5]}]
# IO_L6P_T0_15
# set_property PACKAGE_PIN D15 [get_ports {k_fmc_la_p[5]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[5]}]
# IO_L4N_T0_AD9N_15
# set_property PACKAGE_PIN B19 [get_ports {k_fmc_la_n[6]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[6]}]
# IO_L4P_T0_AD9P_15
# set_property PACKAGE_PIN C19 [get_ports {k_fmc_la_p[6]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[6]}]
# IO_L3N_T0_DQS_AD1N_15
# set_property PACKAGE_PIN A17 [get_ports {k_fmc_la_n[7]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[7]}]
# IO_L3P_T0_DQS_AD1P_15
# set_property PACKAGE_PIN B17 [get_ports {k_fmc_la_p[7]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[7]}]
# IO_L17N_T2_A25_15
# set_property PACKAGE_PIN E20 [get_ports {k_fmc_la_n[8]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[8]}]
# IO_L17P_T2_A26_15
# set_property PACKAGE_PIN F19 [get_ports {k_fmc_la_p[8]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[8]}]
# IO_L2N_T0_AD8N_15
# set_property PACKAGE_PIN A19 [get_ports {k_fmc_la_n[9]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[9]}]
# IO_L2P_T0_AD8P_15
# set_property PACKAGE_PIN A18 [get_ports {k_fmc_la_p[9]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[9]}]
# IO_L5N_T0_AD2N_15
# set_property PACKAGE_PIN C18 [get_ports {k_fmc_la_n[10]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[10]}]
# IO_L5P_T0_AD2P_15
# set_property PACKAGE_PIN C17 [get_ports {k_fmc_la_p[10]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[10]}]
# IO_L7N_T1_AD10N_15
# set_property PACKAGE_PIN G16 [get_ports {k_fmc_la_n[11]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[11]}]
# IO_L7P_T1_AD10P_15
# set_property PACKAGE_PIN H16 [get_ports {k_fmc_la_p[11]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[11]}]
# IO_L1N_T0_AD0N_15
# set_property PACKAGE_PIN B16 [get_ports {k_fmc_la_n[12]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[12]}]
# IO_L1P_T0_AD0P_15
# set_property PACKAGE_PIN C16 [get_ports {k_fmc_la_p[12]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[12]}]
# IO_L23N_T3_16
# set_property PACKAGE_PIN A15 [get_ports {k_fmc_la_n[13]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[13]}]
# IO_L23P_T3_16
# set_property PACKAGE_PIN B15 [get_ports {k_fmc_la_p[13]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[13]}]
# IO_L8N_T1_AD3N_15
# set_property PACKAGE_PIN F15 [get_ports {k_fmc_la_n[14]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[14]}]
# IO_L8P_T1_AD3P_15
# set_property PACKAGE_PIN G15 [get_ports {k_fmc_la_p[14]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[14]}]
# IO_L21N_T3_DQS_16
# set_property PACKAGE_PIN A14 [get_ports {k_fmc_la_n[15]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[15]}]
# IO_L21P_T3_DQS_16
# set_property PACKAGE_PIN B14 [get_ports {k_fmc_la_p[15]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[15]}]
# IO_L18N_T2_16
# set_property PACKAGE_PIN E12 [get_ports {k_fmc_la_n[16]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[16]}]
# IO_L18P_T2_16
# set_property PACKAGE_PIN E13 [get_ports {k_fmc_la_p[16]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[16]}]
# IO_L13N_T2_MRCC_16
# set_property PACKAGE_PIN C11 [get_ports {k_fmc_la_n[17]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[17]}]
# IO_L13P_T2_MRCC_16
# set_property PACKAGE_PIN C12 [get_ports {k_fmc_la_p[17]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[17]}]
# IO_L12N_T1_MRCC_16
# set_property PACKAGE_PIN D10 [get_ports {k_fmc_la_n[18]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[18]}]
# IO_L12P_T1_MRCC_16
# set_property PACKAGE_PIN E10 [get_ports {k_fmc_la_p[18]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[18]}]
# IO_L8N_T1_16
# set_property PACKAGE_PIN D8 [get_ports {k_fmc_la_n[19]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[19]}]
# IO_L8P_T1_16
# set_property PACKAGE_PIN D9 [get_ports {k_fmc_la_p[19]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[19]}]
# IO_L2N_T0_16
# set_property PACKAGE_PIN G9 [get_ports {k_fmc_la_n[20]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[20]}]
# IO_L2P_T0_16
# set_property PACKAGE_PIN G10 [get_ports {k_fmc_la_p[20]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[20]}]
# IO_L24N_T3_16
# set_property PACKAGE_PIN A12 [get_ports {k_fmc_la_n[21]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[21]}]
# IO_L24P_T3_16
# set_property PACKAGE_PIN A13 [get_ports {k_fmc_la_p[21]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[21]}]
# IO_L7N_T1_16
# set_property PACKAGE_PIN F8 [get_ports {k_fmc_la_n[22]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[22]}]
# IO_L7P_T1_16
# set_property PACKAGE_PIN F9 [get_ports {k_fmc_la_p[22]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[22]}]
# IO_L9N_T1_DQS_AD11N_15
# set_property PACKAGE_PIN J16 [get_ports {k_fmc_la_n[23]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[23]}]
# IO_L9P_T1_DQS_AD11P_15
# set_property PACKAGE_PIN J15 [get_ports {k_fmc_la_p[23]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[23]}]
# IO_L1N_T0_16
# set_property PACKAGE_PIN H8 [get_ports {k_fmc_la_n[24]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[24]}]
# IO_L1P_T0_16
# set_property PACKAGE_PIN H9 [get_ports {k_fmc_la_p[24]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[24]}]
# IO_L15N_T2_DQS_16
# set_property PACKAGE_PIN F13 [get_ports {k_fmc_la_n[25]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[25]}]
# IO_L15P_T2_DQS_16
# set_property PACKAGE_PIN F14 [get_ports {k_fmc_la_p[25]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[25]}]
# IO_L3N_T0_DQS_16
# set_property PACKAGE_PIN H13 [get_ports {k_fmc_la_n[26]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[26]}]
# IO_L3P_T0_DQS_16
# set_property PACKAGE_PIN J13 [get_ports {k_fmc_la_p[26]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[26]}]
# IO_L20N_T3_16
# set_property PACKAGE_PIN B11 [get_ports {k_fmc_la_n[27]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[27]}]
# IO_L20P_T3_16
# set_property PACKAGE_PIN B12 [get_ports {k_fmc_la_p[27]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[27]}]
# IO_L10N_T1_16
# set_property PACKAGE_PIN B9 [get_ports {k_fmc_la_n[28]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[28]}]
# IO_L10P_T1_16
# set_property PACKAGE_PIN C9 [get_ports {k_fmc_la_p[28]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[28]}]
# IO_L9N_T1_DQS_16
# set_property PACKAGE_PIN A8 [get_ports {k_fmc_la_n[29]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[29]}]
# IO_L9P_T1_DQS_16
# set_property PACKAGE_PIN A9 [get_ports {k_fmc_la_p[29]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[29]}]
# IO_L16N_T2_16
# set_property PACKAGE_PIN F12 [get_ports {k_fmc_la_n[30]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[30]}]
# IO_L16P_T2_16
# set_property PACKAGE_PIN G12 [get_ports {k_fmc_la_p[30]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[30]}]
# IO_L4N_T0_16
# set_property PACKAGE_PIN J10 [get_ports {k_fmc_la_n[31]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[31]}]
# IO_L4P_T0_16
# set_property PACKAGE_PIN J11 [get_ports {k_fmc_la_p[31]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[31]}]
# IO_L22N_T3_16
# set_property PACKAGE_PIN A10 [get_ports {k_fmc_la_n[32]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[32]}]
# IO_L22P_T3_16
# set_property PACKAGE_PIN B10 [get_ports {k_fmc_la_p[32]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[32]}]
# IO_L5N_T0_16
# set_property PACKAGE_PIN G14 [get_ports {k_fmc_la_n[33]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_n[33]}]
# IO_L5P_T0_16
# set_property PACKAGE_PIN H14 [get_ports {k_fmc_la_p[33]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_fmc_la_p[33]}]

# IO_L19P_T3_16
# set_property PACKAGE_PIN C14 [get_ports k_fmc_scl]
# set_property IOSTANDARD LVCMOS25 [get_ports k_fmc_scl]
# IO_L19N_T3_VREF_16
# set_property PACKAGE_PIN C13 [get_ports k_fmc_sda]
# set_property IOSTANDARD LVCMOS25 [get_ports k_fmc_sda]
# IO_25_16
# set_property PACKAGE_PIN J14 [get_ports k_fmc_prsnt_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_fmc_prsnt_b]
# IO_25_13
# set_property PACKAGE_PIN U16 [get_ports k_fmc_pg_en]
# set_property IOSTANDARD LVCMOS25 [get_ports k_fmc_pg_en]

##############
# DDR3 SDRAM #
##############
# IO_L11P_T1_SRCC_33
# set_property PACKAGE_PIN AA9 [get_ports {k_ddr3_dq[0]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[0]}]
# IO_L12N_T1_MRCC_33
# set_property PACKAGE_PIN AD9 [get_ports {k_ddr3_dq[1]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[1]}]
# IO_L11N_T1_SRCC_33
# set_property PACKAGE_PIN AB9 [get_ports {k_ddr3_dq[2]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[2]}]
# IO_L7N_T1_33
# set_property PACKAGE_PIN AF7 [get_ports {k_ddr3_dq[3]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[3]}]
# IO_L8P_T1_33
# set_property PACKAGE_PIN AA8 [get_ports {k_ddr3_dq[4]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[4]}]
# IO_L8N_T1_33
# set_property PACKAGE_PIN AA7 [get_ports {k_ddr3_dq[5]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[5]}]
# IO_L7P_T1_33
# set_property PACKAGE_PIN AE7 [get_ports {k_ddr3_dq[6]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[6]}]
# IO_L10N_T1_33
# set_property PACKAGE_PIN AC7 [get_ports {k_ddr3_dq[7]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[7]}]
# IO_L6P_T0_33
# set_property PACKAGE_PIN V9 [get_ports {k_ddr3_dq[8]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[8]}]
# IO_L5N_T0_33
# set_property PACKAGE_PIN Y10 [get_ports {k_ddr3_dq[9]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[9]}]
# IO_L2P_T0_33
# set_property PACKAGE_PIN V8 [get_ports {k_ddr3_dq[10]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[10]}]
# IO_L5P_T0_33
# set_property PACKAGE_PIN Y11 [get_ports {k_ddr3_dq[11]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[11]}]
# IO_L4P_T0_33
# set_property PACKAGE_PIN Y8 [get_ports {k_ddr3_dq[12]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[12]}]
# IO_L1P_T0_33
# set_property PACKAGE_PIN V11 [get_ports {k_ddr3_dq[13]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[13]}]
# IO_L2N_T0_33
# set_property PACKAGE_PIN V7 [get_ports {k_ddr3_dq[14]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[14]}]
# IO_L1N_T0_33
# set_property PACKAGE_PIN W11 [get_ports {k_ddr3_dq[15]}]
# set_property IOSTANDARD SSTL15_T_DCI [get_ports {k_ddr3_dq[15]}]

# IO_L20N_T3_34
# set_property PACKAGE_PIN AE1 [get_ports {k_ddr3_a[0]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[0]}]
# IO_L16P_T2_34
# set_property PACKAGE_PIN AB6 [get_ports {k_ddr3_a[1]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[1]}]
# IO_L22P_T3_34
# set_property PACKAGE_PIN AE3 [get_ports {k_ddr3_a[2]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[2]}]
# IO_L16N_T2_34
# set_property PACKAGE_PIN AC6 [get_ports {k_ddr3_a[3]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[3]}]
# IO_L17P_T2_34
# set_property PACKAGE_PIN Y6 [get_ports {k_ddr3_a[4]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[4]}]
# IO_L19P_T3_34
# set_property PACKAGE_PIN AD4 [get_ports {k_ddr3_a[5]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[5]}]
# IO_L15P_T2_DQS_34
# set_property PACKAGE_PIN AA5 [get_ports {k_ddr3_a[6]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[6]}]
# IO_L24P_T3_34
# set_property PACKAGE_PIN AF3 [get_ports {k_ddr3_a[7]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[7]}]
# IO_L15N_T2_DQS_34
# set_property PACKAGE_PIN AB5 [get_ports {k_ddr3_a[8]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[8]}]
# IO_L24N_T3_34
# set_property PACKAGE_PIN AF2 [get_ports {k_ddr3_a[9]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[9]}]
# IO_L19N_T3_VREF_34
# set_property PACKAGE_PIN AD3 [get_ports {k_ddr3_a[10]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[10]}]
# IO_L14P_T2_SRCC_34
# set_property PACKAGE_PIN AC4 [get_ports {k_ddr3_a[11]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[11]}]
# IO_L20P_T3_34
# set_property PACKAGE_PIN AD1 [get_ports {k_ddr3_a[12]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[12]}]
# IO_L22N_T3_34
# set_property PACKAGE_PIN AE2 [get_ports {k_ddr3_a[13]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[13]}]
# IO_L14N_T2_SRCC_34
# set_property PACKAGE_PIN AC3 [get_ports {k_ddr3_a[14]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_a[14]}]

# IO_L21N_T3_DQS_34
# set_property PACKAGE_PIN AF4 [get_ports {k_ddr3_ba[0]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_ba[0]}]
# IO_L17N_T2_34
# set_property PACKAGE_PIN Y5 [get_ports {k_ddr3_ba[1]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_ba[1]}]
# IO_L18N_T2_34
# set_property PACKAGE_PIN AD5 [get_ports {k_ddr3_ba[2]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_ba[2]}]

# IO_L23P_T3_34
# set_property PACKAGE_PIN AE6 [get_ports k_ddr3_ras_b]
# set_property IOSTANDARD SSTL15 [get_ports k_ddr3_ras_b]
# IO_L18P_T2_34
# set_property PACKAGE_PIN AD6 [get_ports k_ddr3_cas_b]
# set_property IOSTANDARD SSTL15 [get_ports k_ddr3_cas_b]
# IO_L21P_T3_DQS_34
# set_property PACKAGE_PIN AF5 [get_ports k_ddr3_we_b]
# set_property IOSTANDARD SSTL15 [get_ports k_ddr3_we_b]
# IO_L10P_T1_33
# set_property PACKAGE_PIN AB7 [get_ports k_ddr3_reset_b]
# set_property IOSTANDARD LVCMOS15 [get_ports k_ddr3_reset_b]
# IO_L2P_T0_34
# set_property PACKAGE_PIN U2 [get_ports k_ddr3_cke]
# set_property IOSTANDARD SSTL15 [get_ports k_ddr3_cke]
# IO_L2N_T0_34
# set_property PACKAGE_PIN U1 [get_ports k_ddr3_odt]
# set_property IOSTANDARD SSTL15 [get_ports k_ddr3_odt]
# IO_L23N_T3_34
# set_property PACKAGE_PIN AE5 [get_ports k_ddr3_cs_b]
# set_property IOSTANDARD SSTL15 [get_ports k_ddr3_cs_b]
# IO_L12P_T1_MRCC_33
# set_property PACKAGE_PIN AC9 [get_ports {k_ddr3_dm[0]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_dm[0]}]
# IO_L4N_T0_33
# set_property PACKAGE_PIN Y7 [get_ports {k_ddr3_dm[1]}]
# set_property IOSTANDARD SSTL15 [get_ports {k_ddr3_dm[1]}]
# IO_L9N_T1_DQS_33
# set_property PACKAGE_PIN AD8 [get_ports {k_ddr3_dqs_n[0]}]
# set_property IOSTANDARD DIFF_SSTL15_T_DCI [get_ports {k_ddr3_dqs_n[0]}]
# IO_L9P_T1_DQS_33
# set_property PACKAGE_PIN AC8 [get_ports {k_ddr3_dqs_p[0]}]
# set_property IOSTANDARD DIFF_SSTL15_T_DCI [get_ports {k_ddr3_dqs_p[0]}]
# IO_L3N_T0_DQS_33
# set_property PACKAGE_PIN W9 [get_ports {k_ddr3_dqs_n[1]}]
# set_property IOSTANDARD DIFF_SSTL15_T_DCI [get_ports {k_ddr3_dqs_n[1]}]
# IO_L3P_T0_DQS_33
# set_property PACKAGE_PIN W10 [get_ports {k_ddr3_dqs_p[1]}]
# set_property IOSTANDARD DIFF_SSTL15_T_DCI [get_ports {k_ddr3_dqs_p[1]}]
# IO_L3N_T0_DQS_34
# set_property PACKAGE_PIN W5 [get_ports k_ddr3_ck_n]
# set_property IOSTANDARD DIFF_SSTL15 [get_ports k_ddr3_ck_n]
# IO_L3P_T0_DQS_34
# set_property PACKAGE_PIN W6 [get_ports k_ddr3_ck_p]
# set_property IOSTANDARD DIFF_SSTL15 [get_ports k_ddr3_ck_p]

##########################
# Kintex-7 Configuration #
##########################
# IO_L24N_T3_A00_D16_14
# set_property PACKAGE_PIN J23 [get_ports {k_conf_a[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[0]}]
# IO_L24P_T3_A01_D17_14
# set_property PACKAGE_PIN K23 [get_ports {k_conf_a[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[1]}]
# IO_L23N_T3_A02_D18_14
# set_property PACKAGE_PIN K22 [get_ports {k_conf_a[2]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[2]}]
# IO_L23P_T3_A03_D19_14
# set_property PACKAGE_PIN L22 [get_ports {k_conf_a[3]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[3]}]
# IO_L22N_T3_A04_D20_14
# set_property PACKAGE_PIN J25 [get_ports {k_conf_a[4]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[4]}]
# IO_L22P_T3_A05_D21_14
# set_property PACKAGE_PIN J24 [get_ports {k_conf_a[5]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[5]}]
# IO_L21N_T3_DQS_A06_D22_14
# set_property PACKAGE_PIN H22 [get_ports {k_conf_a[6]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[6]}]
# IO_L20N_T3_A07_D23_14
# set_property PACKAGE_PIN H24 [get_ports {k_conf_a[7]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[7]}]
# IO_L20P_T3_A08_D24_14
# set_property PACKAGE_PIN H23 [get_ports {k_conf_a[8]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[8]}]
# IO_L19N_T3_A09_D25_VREF_14
# set_property PACKAGE_PIN G21 [get_ports {k_conf_a[9]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[9]}]
# IO_L19P_T3_A10_D26_14
# set_property PACKAGE_PIN H21 [get_ports {k_conf_a[10]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[10]}]
# IO_L18N_T2_A11_D27_14
# set_property PACKAGE_PIN H26 [get_ports {k_conf_a[11]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[11]}]
# IO_L18P_T2_A12_D28_14
# set_property PACKAGE_PIN J26 [get_ports {k_conf_a[12]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[12]}]
# IO_L17N_T2_A13_D29_14
# set_property PACKAGE_PIN E26 [get_ports {k_conf_a[13]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[13]}]
# IO_L17P_T2_A14_D30_14
# set_property PACKAGE_PIN F25 [get_ports {k_conf_a[14]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[14]}]
# IO_L16N_T2_A15_D31_14
# set_property PACKAGE_PIN G26 [get_ports {k_conf_a[15]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[15]}]
# IO_L22N_T3_A16_15
# set_property PACKAGE_PIN K17 [get_ports {k_conf_a[16]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[16]}]
# IO_L22P_T3_A17_15
# set_property PACKAGE_PIN K16 [get_ports {k_conf_a[17]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[17]}]
# IO_L21N_T3_DQS_A18_15
# set_property PACKAGE_PIN L20 [get_ports {k_conf_a[18]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[18]}]
# IO_L20N_T3_A19_15
# set_property PACKAGE_PIN J19 [get_ports {k_conf_a[19]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[19]}]
# IO_L20P_T3_A20_15
# set_property PACKAGE_PIN J18 [get_ports {k_conf_a[20]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[20]}]
# IO_L19N_T3_A21_VREF_15
# set_property PACKAGE_PIN J20 [get_ports {k_conf_a[21]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[21]}]
# IO_L19P_T3_A22_15
# set_property PACKAGE_PIN K20 [get_ports {k_conf_a[22]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_a[22]}]
# IO_L15N_T2_DQS_ADV_B_15
# set_property PACKAGE_PIN D20 [get_ports k_conf_adv_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_adv_b]
# IO_L16P_T2_CSI_B_14
# set_property PACKAGE_PIN G25 [get_ports k_conf_csi_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_csi_b]
# IO_L1P_T0_D00_MOSI_14
# set_property PACKAGE_PIN B24 [get_ports {k_conf_d[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[0]}]
# IO_L1N_T0_D01_DIN_14
# set_property PACKAGE_PIN A25 [get_ports {k_conf_d[1]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[1]}]
# IO_L2P_T0_D02_14
# set_property PACKAGE_PIN B22 [get_ports {k_conf_d[2]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[2]}]
# IO_L2N_T0_D03_14
# set_property PACKAGE_PIN A22 [get_ports {k_conf_d[3]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[3]}]
# IO_L4P_T0_D04_14
# set_property PACKAGE_PIN A23 [get_ports {k_conf_d[4]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[4]}]
# IO_L4N_T0_D05_14
# set_property PACKAGE_PIN A24 [get_ports {k_conf_d[5]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[5]}]
# IO_L5P_T0_D06_14
# set_property PACKAGE_PIN D26 [get_ports {k_conf_d[6]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[6]}]
# IO_L5N_T0_D07_14
# set_property PACKAGE_PIN C26 [get_ports {k_conf_d[7]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[7]}]
# IO_L6N_T0_D08_VREF_14
# set_property PACKAGE_PIN C24 [get_ports {k_conf_d[8]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[8]}]
# IO_L7P_T1_D09_14
# set_property PACKAGE_PIN D21 [get_ports {k_conf_d[9]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[9]}]
# IO_L7N_T1_D10_14
# set_property PACKAGE_PIN C22 [get_ports {k_conf_d[10]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[10]}]
# IO_L8P_T1_D11_14
# set_property PACKAGE_PIN B20 [get_ports {k_conf_d[11]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[11]}]
# IO_L8N_T1_D12_14
# set_property PACKAGE_PIN A20 [get_ports {k_conf_d[12]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[12]}]
# IO_L9N_T1_DQS_D13_14
# set_property PACKAGE_PIN E22 [get_ports {k_conf_d[13]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[13]}]
# IO_L10P_T1_D14_14
# set_property PACKAGE_PIN C21 [get_ports {k_conf_d[14]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[14]}]
# IO_L10N_T1_D15_14
# set_property PACKAGE_PIN B21 [get_ports {k_conf_d[15]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_conf_d[15]}]
# IO_L15N_T2_DQS_DOUT_CSO_B_14
# set_property PACKAGE_PIN D25 [get_ports k_conf_dout_cso_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_dout_cso_b]
# IO_L3N_T0_DQS_EMCCLK_14
# set_property PACKAGE_PIN B26 [get_ports k_conf_emcclk]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_emcclk]
# IO_L6P_T0_FCS_B_14
# set_property PACKAGE_PIN C23 [get_ports k_conf_fcs_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_fcs_b]
# IO_L23P_T3_FOE_B_15
# set_property PACKAGE_PIN M17 [get_ports k_conf_foe_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_foe_b]
# IO_L23N_T3_FWE_B_15
# set_property PACKAGE_PIN L18 [get_ports k_conf_fwe_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_fwe_b]
# IO_L3P_T0_DQS_PUDC_B_14
# set_property PACKAGE_PIN B25 [get_ports k_conf_pudc_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_pudc_b]
# IO_L15P_T2_DQS_RDWR_B_14
# set_property PACKAGE_PIN E25 [get_ports k_conf_rdwr_b]
# set_property IOSTANDARD LVCMOS25 [get_ports k_conf_rdwr_b]

############
# Reserved #
############
# Test Pad
# IO_L11N_T1_SRCC_14
# set_property PACKAGE_PIN D24 [get_ports {k_rsvio_n[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_rsvio_n[0]}]
# IO_L11P_T1_SRCC_14
# set_property PACKAGE_PIN D23 [get_ports {k_rsvio_p[0]}]
# set_property IOSTANDARD LVCMOS25 [get_ports {k_rsvio_p[0]}]

# Watchdog Timer
# IO_L6N_T0_VREF_13
# set_property PACKAGE_PIN P25 [get_ports k_wdt_wdi]
# set_property IOSTANDARD LVCMOS25 [get_ports k_wdt_wdi]
# IO_L19P_T3_33
# set_property PACKAGE_PIN AD11 [get_ports k_wdt_wdo_b]
# set_property IOSTANDARD LVCMOS15 [get_ports k_wdt_wdo_b]
