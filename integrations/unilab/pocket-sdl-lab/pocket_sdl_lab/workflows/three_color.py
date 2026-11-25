from unilabos.registry.workflows import workflow, WorkflowGuide

@workflow(display_name="Pocket SDL 三色光谱测量", description="连接、自检、三组测量、保存、关灯断开", guide=WorkflowGuide(preparation=["先使用SIMULATED设备；真机运行前关闭其他占用串口的程序"],expected=["得到三组八通道读数及JSON/CSV记录","设备关灯并断开"],notes=["文件保存在设备运行主机的three-color-data目录","手动取消流程后调用finish_three_color；进程被强制结束时无法保证断开，固件看门狗提供关灯保护"]))
def three_color(ctx):
    ctx.run("pico_spectro_01/begin_three_color", {}, name="连接并开始记录")
    ctx.run("pico_spectro_01/check_three_color", {}, name="自检")
    ctx.run("pico_spectro_01/record_color", {"r":1.0,"g":0.0,"b":0.0}, name="测红色")
    ctx.run("pico_spectro_01/record_color", {"r":0.0,"g":1.0,"b":0.0}, name="测绿色")
    ctx.run("pico_spectro_01/record_color", {"r":0.0,"g":0.0,"b":1.0}, name="测蓝色")
    ctx.run("pico_spectro_01/save_three_color", {}, name="保存读数")
    ctx.run("pico_spectro_01/finish_three_color", {}, name="关灯并断开")
