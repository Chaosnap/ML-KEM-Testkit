# AIST SASEBO-GIII controller reference

Minimal, unmodified subset of the user's original `sasebo_giii` reference package:
controller RTL, active UCF, ISE project, and FT2232H EEPROM template. Original
controller version: 1.0, April 25, 2013. The prebuilt controller image is stored
in `../../firmware/sakura_x/sasebo_giii_ctrl.bit`.

These files and the AIST controller bitstream retain the AIST terms in
[LICENSE.txt](LICENSE.txt), including the restriction on monetary profit. They
are not relicensed under the repository's MIT license. Original notices remain
in the source files. The EEPROM template is provided for manual recovery with
FT_Prog; the SRAM programming script does not write it.

Reference project page recorded by AIST: http://www.risec.aist.go.jp/project/sasebo/

The AES example, Windows executables, and Flash `.mcs` files are not needed for
this SRAM programming flow and are not included in this subset.
