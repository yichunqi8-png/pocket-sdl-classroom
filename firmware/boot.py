"""将实验协议与 CircuitPython REPL 分开；更新后必须复位 Pico。"""

import usb_cdc

usb_cdc.enable(console=True, data=True)
