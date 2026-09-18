# LAN-file-sharing-software
LAN sharing software, which enables interconnection among devices such as mobile phones and tablets within a local area network.

---

## ✨ 特性

### 传输
- **断点续传**：大文件中断后可从断点继续，不用从头传
- **多任务队列**：支持同时排队多个文件，逐个上传
- **暂停 / 继续 / 移除**：单条或整队列控制
- **刷新恢复**：关闭或刷新页面后，点「全部继续」直接续传，无需重新选择文件
- **HTTP Range 支持**：视频边下边播、拖动进度条即时响应

### 权限
- **定向发送**：可选择只发给某台设备，其他设备看不到
- **随时改权限**：上传后也能把公开改成私有，或反过来
- **发送者专属**：只有发送者能修改权限

### 预览
- **图片**：jpg / png / gif / webp / svg / avif…
- **视频**：mp4 / webm / mov… 支持拖动进度条
- **音频**：mp3 / wav / flac / m4a…
- **PDF**：内置 **PDF.js** 渲染，跨平台一致（Android / iOS 也能预览，不受系统浏览器限制）
- **文本 / 代码**：txt / md / json / py / js / html… 支持语法高亮以外的基础展示

### 界面
- 现代 SaaS 风格设计，支持 **深色模式**（跟随系统）
- 桌面 / 平板 / 手机自适应，iPad、Android 均有良好体验
- 自定义下拉菜单，跨平台表现一致
- 在线设备实时列表

---

## 🚀 快速开始

### 环境要求

- **Python 3.8+**（自带 Tkinter，无需额外安装）
- 服务端与客户端在同一局域网内

无需 `pip install`，所有依赖都是 Python 标准库。

### 运行

```bash
# 1. 下载或克隆本项目
git clone https://github.com/yourname/lanshare.git
cd lanshare

# 2. 启动
python transfer.py