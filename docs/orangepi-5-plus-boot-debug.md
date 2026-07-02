# Orange Pi 5 Plus FnNAS 镜像启动问题排查笔记

本文档记录 2026-07-02 对 FnNAS Rockchip / Orange Pi 5 Plus 镜像启动问题的排查结论、证据、已做修复和后续迭代方向。

目标设备：

- 开发板：Orange Pi 5 Plus
- SoC：Rockchip RK3588
- eMMC：64GB
- RAM：16GB
- 用户现象：镜像烧录到 eMMC 后，蓝灯亮，HDMI 无显示；预期厂家系统应红蓝闪烁。
- 用户提供内核源码包：`/home/jiang/Downloads/orange-pi-6.1-rk35xx.tar.gz`
- 官方构建源码：`https://github.com/orangepi-xunlong/orangepi-build`

## 当前结论

目前最可疑且已经修复的点是：FnNAS 给 Rockchip 平台创建 BOOT 分区时使用了宿主机默认 ext4 特性，生成的 BOOT 分区带有 `64bit` 和 `metadata_csum`。Orange Pi 5 Plus 官方构建使用的是 Rockchip 旧 U-Boot `v2017.09-rk3588`，这类 U-Boot 对较新的 ext4 特性支持不完整，可能无法稳定读取 `/boot/boot.scr`、`/boot/Image`、`/boot/uInitrd` 或 DTB。

同时，fnOS/FnNAS 应按 headless NAS 系统对待：日常入口是 Web/App，不是 HDMI 本地桌面。HDMI 无显示不能单独证明系统没启动，应同时检查 DHCP、ARP、SSH 和 Web 端口。

已在 `renas` 中修复为：

```bash
mkfs.ext4 -F -q -O ^64bit,^metadata_csum -U ${BOOT_UUID} -L "BOOT" -b 4k -m 0 ${loop_new}p1
```

修复分支：

- fork：`https://github.com/helywin/fnnas`
- 分支：`fix/rockchip-bootfs-ext4-compat`
- 修复提交：`eb07552 Fix Rockchip bootfs ext4 compatibility`

仍需串口确认的点：

- 如果板载 SPI Flash 内已有旧 U-Boot，RK3588 BootROM 可能先从 SPI 启动，导致 eMMC 里的新镜像完全没有机会执行。
- 如果 SPI loader 已经卡住，eMMC 镜像里的脚本无法自救，只能通过串口、MaskROM、厂家工具或可启动介质处理 SPI。

## 官方 orangepi-build 对照

克隆官方仓库后确认：

- 默认分支：`next`
- HEAD：`bdba421984211da19191dc6ac6818a247817335f`
- `main`：`f00cd197b4a9873f36093d4f4748b733642059a7`

Orange Pi 5 Plus 板级配置：

文件：`external/config/boards/orangepi5plus.conf`

关键配置：

```bash
BOARD_NAME="Orange Pi 5 Plus"
BOARDFAMILY="rockchip-rk3588"
BOOTCONFIG="orangepi_5_plus_defconfig"
KERNEL_TARGET="legacy,current"
BOOT_FDT_FILE="rockchip/rk3588-orangepi-5-plus.dtb"
BOOT_SCENARIO="spl-blobs"
BOOT_SUPPORT_SPI="yes"
```

Rockchip RK3588 family 配置：

文件：`external/config/sources/families/rockchip-rk3588.conf`

current 分支关键配置：

```bash
BOOTBRANCH='branch:v2017.09-rk3588'
KERNELBRANCH='branch:orange-pi-6.1-rk35xx'
KERNELPATCHDIR='rockchip-rk3588-current'
LINUXCONFIG="linux-rockchip-rk3588-current"
KERNEL_USE_GCC='> 10.0'
```

这说明用户提供的 `orange-pi-6.1-rk35xx.tar.gz` 方向是对的，它对应官方 current kernel 分支；但它是完整 Linux 源码树，不是 FnNAS 当前打包脚本能直接消费的二进制 kernel 包。

官方 Rockchip 公共配置：

文件：`external/config/sources/families/include/rockchip64_common.inc`

关键点：

```bash
OFFSET=30
KERNEL_IMAGE_TYPE=Image
BOOTSCRIPT='boot-rockchip64.cmd:boot.cmd'
BOOTENV_FILE='rockchip.txt'
SERIALCON=ttyFIQ0:1500000
```

RK3588 使用：

```bash
DDR_BLOB="${DDR_BLOB:=rk35/rk3588_ddr_lp4_2112MHz_lp5_2400MHz_v1.23.bin}"
BL31_BLOB='rk35/rk3588_bl31_v1.56.elf'
BOOT_SCENARIO="spl-blobs"
```

官方也支持 SPI：

```bash
BOOT_SUPPORT_SPI="yes"
```

官方会额外生成 `rkspi_loader.img`：

```bash
dd if=idbloader.img of=rkspi_loader.img seek=64 conv=notrunc
dd if=u-boot.itb of=rkspi_loader.img seek=1024 conv=notrunc
```

## U-Boot 写入位置对照

FnNAS 当前 model 数据库里 Orange Pi 5 Plus 配置：

文件：`make-fnnas/fnnas-files/common-files/etc/model_database.conf`

```text
r106 : Orange-Pi-5-Plus : rk3588 : rk3588-orangepi-5-plus.dtb : NA : u-boot.itb : idbloader.img : ... : orangepi-5-plus : yes
```

FnNAS Rockchip 写 bootloader：

文件：`renas`

```bash
dd if="${bootloader_path}/${BOOTLOADER_IMG}" of="${loop_new}" conv=fsync,notrunc bs=512 seek=64
dd if="${bootloader_path}/${MAINLINE_UBOOT}" of="${loop_new}" conv=fsync,notrunc bs=512 seek=16384
```

官方 orangepi-build 写 eMMC/SD loader：

文件：`external/config/sources/families/include/rockchip64_common.inc`

```bash
dd if=$1/idbloader.img of=$2 seek=64 conv=notrunc
dd if=$1/u-boot.itb of=$2 seek=16384 conv=notrunc
```

结论：

- FnNAS 的 eMMC/SD loader 写入位置和官方一致。
- 这部分不像当前问题主因。
- 如果问题发生在 BootROM 选择 SPI 阶段，则这些 eMMC 写入位置再正确也不会被执行。

## BOOT 分区 ext4 兼容性问题

FnNAS 修复前：

```bash
mkfs.ext4 -F -q -U ${BOOT_UUID} -L "BOOT" -b 4k -m 0 ${loop_new}p1
```

官方 orangepi-build：

文件：`scripts/debootstrap.sh`

```bash
# metadata_csum and 64bit may need to be disabled explicitly when migrating to newer supported host OS releases
mkopts[ext4]="-q -m 2 -O ^64bit,^metadata_csum"
```

官方格式化 BOOT 分区时会使用这些 mkfs 参数：

```bash
mkfs.${mkfs[$bootfs]} ${mkopts[$bootfs]} ${mkopts_label[$bootfs]:+${mkopts_label[$bootfs]}"$BOOT_FS_LABEL"} ${LOOP}p${bootpart}
```

从 FnNAS Orange Pi 5 Plus 发布镜像的 BOOT 分区 dump 到的特性：

```text
Filesystem features:
has_journal ext_attr resize_inode dir_index filetype extent 64bit flex_bg
sparse_super large_file huge_file dir_nlink extra_isize metadata_csum
```

这说明发布镜像实际带有 `64bit` 和 `metadata_csum`。

风险解释：

- RK3588 官方仍使用 U-Boot `v2017.09-rk3588`。
- 老 U-Boot 的 ext4 读取实现对新特性支持有限。
- 如果 U-Boot 无法读取 BOOT 分区，就会在加载 `boot.scr`、`Image`、`uInitrd`、DTB 前失败。
- 这种失败通常表现为屏幕没有内核输出、LED 状态停留在早期阶段。

已做修复：

```bash
mkfs.ext4 -F -q -O ^64bit,^metadata_csum -U ${BOOT_UUID} -L "BOOT" -b 4k -m 0 ${loop_new}p1
```

修复范围：

- 只影响非 FAT 的 BOOTFS 格式化。
- 当前 `renas` 中 Rockchip 使用 `bootfs_type="ext4"`，因此主要影响 Rockchip。
- 不改变分区表、分区偏移、rootfs、kernel、DTB、U-Boot 写入位置。

## FnNAS BOOT 分区内容

从部分下载的 FnNAS Orange Pi 5 Plus 镜像解出 BOOT 分区后，根目录包含：

```text
armbianEnv.txt
boot.cmd
boot.scr
dtb/
extlinux/extlinux.conf.bak
grub/
System.map-6.18.18-trim
config-6.18.18-trim
initrd.img-6.18.18-trim
uInitrd-6.18.18-trim
vmlinuz-6.18.18-trim
uInitrd -> uInitrd-6.18.18-trim
Image -> vmlinuz-6.18.18-trim
dtb-6.18.18-trim -> dtb
```

`armbianEnv.txt` 关键内容：

```bash
verbosity=7
bootlogo=true
fdtfile=rockchip/rk3588-orangepi-5-plus.dtb
rootdev=UUID=58cfc343-f76c-4e2b-9f1d-11876f310794
rootfstype=btrfs
rootflags=compress=zstd:1
earlycon=on
console=serial
consoleargs=console=ttyFIQ0 console=tty1
docker_optimizations=on
extraargs=rw rootwait
extraboardargs=net.ifnames=0 max_loop=128
overlay_prefix=rk3588
overlays=uart7-m2
```

`boot.cmd` 逻辑：

- 读取 `armbianEnv.txt`
- 生成 `bootargs`
- 加载 `/uInitrd`
- 加载 `/Image`
- 加载 `/dtb/${fdtfile}`
- 应用 overlay
- 执行 `booti`

结论：

- 如果 U-Boot 能正常读 BOOT 分区，启动脚本路径是合理的。
- root UUID 已经和 root btrfs superblock 匹配。

## 发布镜像 bootloader 检查结果

从 FnNAS 发布镜像前部抽取：

- sector 64 处存在 `RKNS` 头，符合 Rockchip loader。
- sector 16384 处存在 FIT magic `d0 0d fe ed`，符合 `u-boot.itb`。

抽出的文件 hash：

```text
idbloader.img:
5b26987186343051b3a2a3f12f6710e4f12e5d8629eba534e642b90b5816e8d4

u-boot.itb:
5cd0e166332355fcf4c540b3e16a4655452b2ab3803499f99c0f7fbde4d0ef0d
```

这两个文件和 `ophub/u-boot` 中 `rockchip/orangepi-5-plus/` 对应文件一致。

`idbloader.img` 字符串：

```text
DDR Version V1.08 20220617
U-Boot SPL 2017.09-g73cc10cb06-220414 #root (Oct 19 2022 - 16:15:35)
```

`u-boot.itb` 字符串中可见：

```text
board=evb_rk3588
board_name=evb_rk3588
fdtfile=rockchip/rk3588-rock-5b.dtb
boot_scripts=boot.scr.uimg boot.scr
boot_targets=mmc1 mmc0 usb0 nvme mtd2 mtd1 mtd0 pxe dhcp
```

注意：

- `u-boot.itb` 内置默认 `fdtfile` 是 Rock 5B，但启动脚本会读取 `armbianEnv.txt` 并覆盖为 `rk3588-orangepi-5-plus.dtb`。
- 因此默认字符串不是直接错误。
- 真正要确认的是 U-Boot 是否成功读取到了 `boot.scr` 和 `armbianEnv.txt`。

## 用户提供的 orange-pi-6.1-rk35xx.tar.gz

文件：

```text
/home/jiang/Downloads/orange-pi-6.1-rk35xx.tar.gz
```

检查结论：

- 它是完整 Linux 6.1 源码树。
- 包内有 `arch/arm64/boot/dts/rockchip/rk3588-orangepi-5-plus.dts` 等 DTS。
- 它不是 FnNAS `replace_kernel` 期待的 kernel artifact 包。

FnNAS `replace_kernel` 期待目录结构中有四类压缩包：

```text
boot-${PLATFORM}-${kernel}*.tar.gz
dtb-${PLATFORM}-${kernel_name}.tar.gz
modules-${PLATFORM}-${kernel_name}.tar.gz
header-${PLATFORM}-${kernel_name}.tar.gz
```

对应逻辑：

```bash
kernel_boot="$(ls ${kernel_path}/${kernel}/${kernel}-${PLATFORM}/boot-${PLATFORM}-${kernel}*.tar.gz | head -n 1)"
kernel_dtb="${kernel_path}/${kernel}/${kernel}-${PLATFORM}/dtb-${PLATFORM}-${kernel_name}.tar.gz"
kernel_modules="${kernel_path}/${kernel}/${kernel}-${PLATFORM}/modules-${PLATFORM}-${kernel_name}.tar.gz"
kernel_header="${kernel_path}/${kernel}/${kernel}-${PLATFORM}/header-${PLATFORM}-${kernel_name}.tar.gz"
```

因此如果要使用厂家 6.1 源码，需要先通过官方或兼容流程编译出 FnNAS 需要的四个 artifact 包，而不是直接把源码包放进 kernel 目录。

## DTB / LED / HDMI 线索

厂家 `rk3588-orangepi-5-plus.dts` 里 LED 配置：

```dts
leds: gpio-leds {
    compatible = "gpio-leds";
    status = "okay";

    blue_led@1 {
        gpios = <&gpio3 RK_PA6 GPIO_ACTIVE_HIGH>;
        label = "blue_led";
        linux,default-trigger = "heartbeat";
    };

    green_led@2 {
        gpios = <&gpio3 RK_PB1 GPIO_ACTIVE_HIGH>;
        label = "green_led";
        linux,default-trigger = "heartbeat";
    };
};
```

FnNAS 镜像里解出的 DTB 反编译后，LED 节点不同：

```dts
gpio-leds {
    compatible = "gpio-leds";

    led {
        color = <0x03>;
        function = "indicator";
        function-enumerator = <0x01>;
        status = "okay";
        gpios = <...>;
    };
};

pwm-leds {
    compatible = "pwm-leds";

    led-1 {
        linux,default-trigger = "heartbeat";
        status = "disabled";
    };
};
```

结论：

- 用户看到“厂家系统红蓝闪烁”是合理线索。
- 但 FnNAS 镜像的 DTB 并不等同于厂家 DTS，LED 行为不同不能单独证明系统没启动。
- 需要配合串口、网口 DHCP、服务端口判断。

FnNAS 镜像解出的 DTB 中已看到：

- `mmc@fe2e0000` status okay，eMMC 节点启用。
- `hdmi@fde80000` status okay。
- `hdmi@fdea0000` status okay。

这说明 DTB 至少不是简单地完全关闭 eMMC 或 HDMI。

## Headless 系统判断

fnOS/FnNAS 的使用形态更接近 headless NAS 系统，而不是接显示器使用的桌面发行版。后续验证不能只看 HDMI 有没有画面。

官方资料依据：

- `如何安装 App 并连接到飞牛 NAS`：说明安装并完成初始化后，通过路由器找到飞牛 NAS 的 IP，可用 IP、域名或 FN ID 连接；HTTP 默认端口为 `5666`，HTTPS 默认端口为 `5667`，并兼容旧的 `8000` / `8001` 端口。
  - https://help.fnnas.com/articles/v1/start/install-app
- `如何修改飞牛系统的端口`：说明 V0.8.22 之后默认 HTTP 端口改为 `5666`，HTTPS 端口改为 `5667`；公测阶段仍继续占用 `8000` 和 `8001`。
  - https://help.fnnas.com/articles/v1/settings/port-customization
- `Rockchip 瑞芯微系列 TF 卡刷教程`：刷好后直接插入设备启动，未把 HDMI 图形界面作为日常入口。
  - https://help.fnnas.com/articles/v1/contact/arm-rk-tf
- `Amlogic 利用 USB U盘刷机教程`：完成 eMMC 烧录后，最后通过 IP 访问 fnOS 飞牛 NAS 界面。
  - https://help.fnnas.com/articles/v1/contact/arm-amlogic-usb

本仓库 README 也采用同样路径：

```text
在路由器管理界面中查找新上线的名为 debian 的设备，获取其 IP 地址，
然后通过浏览器访问 http://192.168.1.15:5666 进入飞牛账号创建界面。
```

文件位置：

- `README.cn.md`
- `README.md`

判断规则：

- HDMI 无画面，但 DHCP 出现、端口 `5666` / `5667` / `8000` / `8001` 可访问：更像正常 headless 启动或 HDMI/DTB/显示链路问题。
- HDMI 无画面，且路由器无 DHCP、无 ARP、Web/SSH 端口全不通：仍应优先按启动链问题排查。
- 蓝灯常亮或不红蓝闪烁不能单独定性，因为当前 FnNAS DTB 的 LED 配置和厂家 6.1 DTS 不一致。

## 局域网设备发现

官方明确提供 App 级局域网发现能力：

- `如何安装 App 并连接到飞牛 NAS` 文档说明，App 登录页可进入 `发现局域网内设备`，会自动发现并展示同局域网下的飞牛 NAS 设备。
  - https://help.fnnas.com/articles/v1/start/install-app

需要注意：

- 官方文档确认“App 能发现设备”，但没有公开说明底层使用的是 mDNS、SSDP、WS-Discovery、NetBIOS 还是私有广播。
- 当前镜像 rootfs 片段中通过 `strings` 能看到发现相关服务名，这是协议能力线索，不等同于确认 App 使用的具体协议。

镜像线索：

```text
/lib/systemd/system/avahi-daemon.service
/etc/systemd/system/upnp.service
/etc/systemd/system/wsdd2.service
/etc/systemd/system/minidlna.service
/etc/systemd/system/nmbd.service
/etc/systemd/system/smbd.service
```

这些服务大致对应：

- `avahi-daemon`：mDNS / DNS-SD。
- `upnp`、`minidlna`：UPnP / SSDP / DLNA 方向。
- `wsdd2`：Windows WS-Discovery。
- `nmbd` / `smbd`：NetBIOS / SMB 发现和文件共享。

推荐查找 IP 的顺序：

1. 使用手机 fnOS App：登录页底部 `发现局域网内设备`。
2. 查看路由器 DHCP 租约，设备名可能显示为 `debian`、`fnos`、`fnnas` 或板卡 hostname。
3. 在同网段主机上做 ARP/邻居表/端口扫描。
4. 再尝试 mDNS、SSDP、NetBIOS、WS-Discovery。

Linux 常用命令：

```bash
# 已知网络邻居
arp -a
ip neigh

# 主动扫描本地二层网段
sudo arp-scan --localnet

# Web/SSH 端口扫描
nmap -p 22,80,443,5666,5667,8000,8001 192.168.1.0/24

# mDNS / DNS-SD
avahi-browse -art | grep -iE 'fnos|fnnas|debian|http|smb'

# NetBIOS
nmblookup '*'
nbtscan 192.168.1.0/24

# SSDP / UPnP
gssdp-discover -t ssdp:all
```

没有 `gssdp-discover` 时，可用 UDP multicast 发 SSDP M-SEARCH：

```bash
printf 'M-SEARCH * HTTP/1.1\r\nHOST:239.255.255.250:1900\r\nMAN:"ssdp:discover"\r\nMX:2\r\nST:ssdp:all\r\n\r\n' \
| socat - UDP4-DATAGRAM:239.255.255.250:1900,ip-multicast-ttl=2
```

判断规则：

- App 或任一发现协议能找到设备：说明系统至少已经启动到网络服务阶段。
- DHCP/ARP/端口/mDNS/SSDP/NetBIOS/WS-Discovery 全无：更像启动链未完成、网卡驱动未起来、网络未接通或设备不在同一二层网络。
- 如果发现协议能看到设备但 `5666` / `5667` 不通，应转向 Web 服务、firewall、nginx 或 fnOS 服务本身排查。

## SPI Flash 风险

Orange Pi 5 Plus 官方配置明确：

```bash
BOOT_SUPPORT_SPI="yes"
```

RK3588 启动链中，如果 SPI NOR 中存在有效 loader，BootROM 可能先从 SPI 启动。此时：

- eMMC 中 `idbloader.img`、`u-boot.itb` 写得再正确，也可能不会被执行。
- eMMC 镜像里的任何“保护脚本”也不会运行。
- 如果 SPI loader 旧、不兼容或配置错误，现象可能和 eMMC 镜像坏类似。

因此 SPI 问题分两种：

1. SPI 已经抢先启动并卡住
   - eMMC 镜像无法自救。
   - 需要串口确认 U-Boot 来源。
   - 可能需要进入 MaskROM、短接/按键方式绕过 SPI，或用厂家工具擦写 SPI。

2. 系统能启动，但 SPI 内存在潜在旧 loader
   - 可以在 FnNAS 用户态做检测、备份、提示、手动修复工具。
   - 不建议自动擦写 SPI。

## 建议的 SPI guard 设计

建议新增一个只读默认、安全优先的 SPI 保护工具，例如：

```bash
fnnas-spi check
fnnas-spi backup
fnnas-spi flash --board orangepi-5-plus --image /usr/lib/u-boot/spi/spi_image.img
```

默认行为：

- `check`：只检测，不修改设备。
- `backup`：只备份 `/dev/mtdX`，不写入。
- `flash`：必须显式执行，且必须多重确认。

检测项：

- `/proc/mtd`
- `/sys/class/mtd/mtd*/name`
- `/sys/class/mtd/mtd*/size`
- `/dev/mtd0` 或 `/dev/mtdblock0`
- `/proc/device-tree/compatible`
- `/usr/lib/u-boot` 是否存在当前板子的 SPI 镜像

备份策略：

```bash
mkdir -p /root/fnnas-backup
dd if=/dev/mtd0 of=/root/fnnas-backup/spi-mtd0-$(date +%Y%m%d-%H%M%S).bin bs=1M status=progress
sha256sum /root/fnnas-backup/spi-mtd0-*.bin > /root/fnnas-backup/spi-sha256sums.txt
```

写入前强制条件：

- 已存在 SPI 备份。
- 板型匹配 Orange Pi 5 Plus。
- `/proc/device-tree/compatible` 包含 `orangepi-5-plus` 或 `rk3588` 相关兼容字符串。
- SPI 镜像大小不超过 mtd 分区大小。
- SPI 镜像 hash 在白名单或随包 sha256 中。
- 用户输入完整确认字符串，例如：

```text
I understand this may brick Orange-Pi-5-Plus
```

不建议做的事：

- 首次启动自动擦 SPI。
- `fnnas-update` 自动写 SPI。
- 检测到 SPI 后自动覆盖。
- 在无法识别板型时提供 `--force` 默认路径。

## 烧录和验证建议

### 烧录前

确认不要把 `.img.gz` 当裸 `.img` 直接写入：

```bash
gzip -dc fnnas_rockchip_orangepi-5-plus_*.img.gz | sudo dd of=/dev/mmcblkX bs=4M conv=fsync status=progress
```

或使用能识别 gzip 镜像的烧录工具。

如果已经烧录到 eMMC，可从其他系统读回前部检查：

```bash
sudo dd if=/dev/mmcblkX of=/tmp/emmc-head.bin bs=1M count=32
od -An -tx1 -N16 -j $((64*512)) /tmp/emmc-head.bin
od -An -tx1 -N16 -j $((16384*512)) /tmp/emmc-head.bin
```

期望：

- sector 64 附近有 `52 4b 4e 53`，即 ASCII `RKNS`。
- sector 16384 附近有 `d0 0d fe ed`，即 FIT image magic。

### BOOT 分区 ext4 特性验证

修复后新镜像应不再包含 `64bit metadata_csum`：

```bash
sudo losetup -Pf --show image.img
sudo dumpe2fs -h /dev/loopXp1 | grep 'Filesystem features'
```

不应出现：

```text
64bit
metadata_csum
```

### root UUID 验证

从 BOOT 分区读取：

```bash
cat /boot/armbianEnv.txt | grep rootdev
```

从 rootfs 分区读取：

```bash
sudo btrfs inspect-internal dump-super /dev/loopXp2 | grep -E 'uuid|label'
```

两者 UUID 应一致。

### 串口验证

建议接串口，波特率：

```text
1500000 8N1
```

需要确认：

- SPL 是否来自 SPI 还是 eMMC。
- U-Boot 是否扫描到 `mmc1` / `mmc0`。
- 是否成功找到 `boot.scr`。
- 是否能加载 `Image`、`uInitrd`、`rk3588-orangepi-5-plus.dtb`。
- 如果失败，失败信息是 ext4、filesystem、bad magic、load error，还是 kernel panic。

关键日志线索：

```text
U-Boot SPL 2017.09
Boot script loaded from ...
Found U-Boot script /boot.scr
load ... /Image
load ... /uInitrd
load ... /dtb/rockchip/rk3588-orangepi-5-plus.dtb
Starting kernel ...
```

如果完全看不到 eMMC loader 相关日志，而看到 SPI/mtd 相关启动路径，应优先处理 SPI。

### 网络验证

FnNAS 可能已经启动但无 HDMI 或 LED 行为不同。应检查：

- 路由器 DHCP 租约。
- 设备是否有 ARP。
- App 是否能在 `发现局域网内设备` 中看到设备。
- mDNS / SSDP / NetBIOS / WS-Discovery 是否能发现设备。
- SSH 是否开放。
- FnNAS Web 服务端口是否开放。重点检查 `5666`、`5667`，同时兼容检查 `8000`、`8001`。

示例：

```bash
arp -a
nmap -p 22,80,443,5666,5667,8000,8001 <device-ip>
```

## 后续迭代建议

优先级 1：验证 ext4 修复

- 用当前分支重新构建 Orange Pi 5 Plus 镜像。
- 烧录到 eMMC。
- 验证 BOOT 分区不含 `64bit metadata_csum`。
- 接串口确认是否能进入 `boot.scr`。

优先级 2：加 SPI guard 文档和工具

- 增加 `docs/rockchip-spi.md` 或扩展本文档。
- 增加 `usr/sbin/fnnas-spi`。
- 默认只检测和备份。
- 不自动写 SPI。

优先级 3：厂家 6.1 kernel artifact 化

- 基于 `orange-pi-6.1-rk35xx` 编译 kernel。
- 产出 FnNAS 需要的四类包：
  - `boot-rockchip-*.tar.gz`
  - `dtb-rockchip-*.tar.gz`
  - `modules-rockchip-*.tar.gz`
  - `header-rockchip-*.tar.gz`
- 确认 DTB 中 LED、HDMI、eMMC、2.5G NIC、PCIe/NVMe、USB 等节点。

优先级 4：串口日志归档

建议把失败和成功串口日志分别保存到：

```text
docs/logs/orangepi-5-plus-fail-spi-or-bootfs.log
docs/logs/orangepi-5-plus-success-after-ext4-fix.log
```

后续判断会更快。

## 常用排查命令

下载慢时使用本机代理 `20080`：

```bash
curl --proxy http://127.0.0.1:20080 -L -C - -o image.img.gz URL
git -c http.proxy=http://127.0.0.1:20080 clone URL
git -c http.proxy=http://127.0.0.1:20080 push
```

查看镜像前部 magic：

```bash
od -An -tx1 -N64 -j $((64*512)) image.img
od -An -tx1 -N16 -j $((16384*512)) image.img
```

抽 BOOT 分区：

```bash
dd if=image.img of=boot.part bs=1M skip=16 count=512 status=progress
debugfs -R 'ls -l /' boot.part
debugfs -R 'cat /armbianEnv.txt' boot.part
```

查看 BOOT ext4 特性：

```bash
dumpe2fs -h boot.part | grep 'Filesystem features'
```

反编译 DTB：

```bash
dtc -I dtb -O dts -o rk3588-orangepi-5-plus.dts rk3588-orangepi-5-plus.dtb
```

查看 kernel 版本：

```bash
file vmlinuz-*
strings vmlinuz-* | grep -m1 'Linux version'
```

检查 U-Boot 字符串：

```bash
strings -a u-boot.itb | grep -E 'boot_targets|boot_scripts|fdtfile|mmc0|mmc1|mtd'
strings -a idbloader.img | grep -E 'DDR Version|U-Boot SPL'
```

## 注意事项

- 目前“BOOTFS ext4 特性不兼容”是强证据支持的主假设，但没有串口日志前不能声称 100% 根因。
- “蓝灯常亮 / 没有红蓝闪烁”不是充分证据，因为 FnNAS 镜像 DTB 的 LED 配置和厂家 DTS 不同。
- “HDMI 无输出”也不是充分证据，fnOS/FnNAS 本身应按 headless NAS 系统验证，应同时查 DHCP/ARP/SSH/Web。
- SPI Flash 一旦涉及擦写，必须先备份，且默认流程不要自动执行。
