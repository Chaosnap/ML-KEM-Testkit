# FT2232H identification for automatic detection

[`ft2232h_config/sakura-x-shell.xml`](../ft2232h_config/sakura-x-shell.xml) is an FT_PROG EEPROM
template for the SAKURA-X shell. It sets the USB product description to
`SAKURA-X Shell`, so software can distinguish the shell from other devices
using the same FTDI VID/PID.

[`ft2232h_config/original.xml`](../ft2232h_config/original.xml) preserves the
original base configuration for reference or restoring the original settings.
Use `sakura-x-shell.xml` when configuring a board for automatic detection.
The base template does not replace a backup of an individual board's settings
and serial number.

The identification contract is:

| Field | Value / purpose |
| --- | --- |
| Vendor ID | `0403` |
| Product ID | `6010` |
| USB product string | `SAKURA-X Shell` (exact match) |
| USB serial number | Unique per board; pairs the two channels and selects a board when several are connected |
| USB interface number `00` | Channel A: existing FIFO communication |
| USB interface number `01` | Channel B: UART, reserved for reset control |

Do not identify a channel by its `/dev/ttyUSB*` number: these numbers depend
on enumeration order. Both channels belong to the same USB device and share
its product string and USB serial number. The product string alone does not
distinguish A from B. Devices with the old `USB <-> Serial Converter` product
string should require explicit port selection instead of automatic matching.

## Apply the template

1. In FT_PROG, scan the connected devices and select the intended FT2232H device. 
2. We recommend exporting the current EEPROM contents to a file for backup before programming. Right-click the device and select **Save As Template** to save the current settings to a file.
3. Apply `ft2232h_config/sakura-x-shell.xml` to the selected device. Right-click the device and select **Apply Template → From File**. Then, select the template file of "`sakura-x-shell.xml`" and click **Open**.
4. Check the Product Description field. It should display "`SAKURA-X Shell`".
5. Program the device. Right-click the device and select **Program Device**. After programming, the device will be reset and re-enumerated with the new settings.

If you want to fix the USB serial number, go to the **USB String Descriptors** and uncheck the **Auto Generate Serial Number** option. Then, enter a serial number in the **Serial Number** field. The serial number must be unique for each board. 

See the [FT_PROG user guide](https://ftdichip.com/wp-content/uploads/2020/07/AN_124_User_Guide_For_FT_PROG.pdf)
for template application and programming instructions.

The template retains the supplied Channel A FIFO mode, Channel B UART mode,
driver selections, and electrical settings. In particular, both driver
selections remain D2XX; this change does not configure Windows VCP support.

## Update udev rules on Linux

The udev rules are maintained in the parent
[chipwhisperer-enhanced-plugins repository](https://github.com/hal-lab-u-tokyo/chipwhisperer-enhanced-plugins).
Before verifying the device, follow its
[udev installation instructions](https://github.com/hal-lab-u-tokyo/chipwhisperer-enhanced-plugins/blob/master/docs/setup.md#installing-udev-rules-linux-only)
to install or update `udev-rules/99-sakura-x.rules`, reload the rules, and
disconnect and reconnect the USB device.

For a board with serial number `FT7A1234`, the rules create
`/dev/sakura-x-shell/FT7A1234/data` for Channel A and
`/dev/sakura-x-shell/FT7A1234/reset` for Channel B. Each board uses its own
serial-number directory.

