# UniLabOS 三色测量拓展（适配本机已验证的 0.12.3 SDK）

普通七任务工作台不依赖UniLabOS。拓展任务需要在装有UniLabOS 0.12.3的Python环境运行；不要在普通学生启动器环境直接执行。

## 安装与启动

在解压后的Pocket-SDL-Classroom目录执行（python必须指向UniLabOS环境）：

```sh
python -m pip install -e .
python -m pip install -e integrations/unilab/pocket-sdl-lab
unilab package inspect --path integrations/unilab/pocket-sdl-lab --out dist/unilab
python integrations/unilab/pocket-sdl-lab/deployment/start.py --mode SIMULATED
```

此版本不支持旧说明中的 `unilab package build` 和 `unilab workspace start`。
启动脚本准备独立community manifest与数据库，管理端口8788、Hostlink端口8789。前端应连接这个后端，而不是其他正在运行的UniLabOS实例。

## 保存并执行

在UniLabOS工作流模板中找到“Pocket SDL 三色光谱测量”，绑定到pico_spectro_01，实例化并保存为本组工作流，再执行。
模板UUID：3556c7e5-f110-5bee-8334-a4404fe0e3a7。

流程：连接并开始记录 → 自检 → 红[1,0,0] → 绿[0,1,0] → 蓝[0,0,1] → 保存读数 → 关灯并断开。
学生可在画布修改三组RGB；每组分量范围0–1。每次测完默认立即关灯，末尾再执行最终清理。
文件保存在设备运行主机工作目录下的three-color-data/<独立运行目录>/，不是浏览器下载目录。record.json含模式、设备信息、时间、RGB、原始八通道读数与错误；readings.csv供表格查看。每测一组立即保存，失败不补零。

动作报错时会尝试关灯、断开。手动取消调度后请调用finish_three_color；进程被强制结束时软件不能保证执行清理，不能将看门狗视为完成断开。

## 明天真机联调

1. 用普通工作台先确认Pico可以自检和读数，再关闭真机工作台，释放串口。
2. 普通插USB，不按BOOTSEL。固定遮光和相对位置。
3. 停止仿真实例，改用 `python integrations/unilab/pocket-sdl-lab/deployment/start.py --mode PHYSICAL`。
4. 多设备时先在production.json填写serial_number或明确port。
5. 在UniLabOS确认设备模式PHYSICAL，先调用connect、doctor、stop、disconnect验收。
6. 保存并执行三色模板，核对3组×8通道、灯依次变化、CSV/JSON标明PHYSICAL、最后灯灭且断开。
7. 失败时保留日志和部分记录，先查线缆/串口占用/固件，不自动切仿真。

## 不依赖UniLabOS的驱动预检

```sh
python -m pocket_sdl.three_color --mode SIMULATED
# 真机须确认串口未被占用
python -m pocket_sdl.three_color --mode PHYSICAL
```
这仅验证驱动与记录逻辑，不能代替UniLabOS调度和真机验收。
