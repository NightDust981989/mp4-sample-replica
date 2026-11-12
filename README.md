# mp4-sample-replica

复刻的 MP4 容器改写脚本：让短视频平台跳过转码，直接分发上传的原始文件。

> 非官方项目，与 ShoreUploader 及其作者无任何关联。代码中出现的标识串是复刻其输出指纹的一部分，用于还原文件结构。

## 背景

向短视频平台上传视频时，平台通常会重新编码，画质会被压低。这类脚本只改容器、不动视频数据，让平台的转码流水线读到一个"不正常"的样本表，从而失败或回退，最终把原始文件原样保存并按原字节下发。

实测中，抖音（以及同一套转码链路的平台）对部分文件会**逐字节**返回上传件，`Content-Range` 总长度与本地文件完全一致，MD5 相同。

仓库里有三个版本，对应 ShoreUploader 的三代做法：

| 脚本 | 对应版本 | 核心思路 | 输出指纹 |
|---|---|---|---|
| `shoreuploader_v200.py` | v2.0.0 | 把 `mvhd` 改成版本 1 + 无限时长，元数据打标 | `ShoreUploader-v2.0.0` |
| `shoreuploader_v203.py` | v2.0.3 | 视频 `stsz`/`stco` 注入空壳样本 | `ShoreUploader-v2.0.3`、`shoreuploader-coded` |
| `shoreuploader_v04.py` | v0.4（当前站内版本） | 音频轨加 18 个空壳样本，并克隆一条音频轨 | `Lavf62.3.100`、`Patched By ShoreUploader` |

三个脚本都只依赖 Python 标准库，`mdat` 里的音视频数据原样复制。

## 用法

```
python -I shoreuploader_v200.py <输入.mp4> <输出.mp4>
python -I shoreuploader_v203.py <输入.mp4> <输出.mp4> [假样本数]
python -I shoreuploader_v04.py  <输入.mp4> <输出.mp4> [标签文字] [--no-preprocess] [--ffmpeg PATH]
```

- `-I` 让解释器不加载当前目录的 `sitecustomize`/`usercustomize`，处理来源不明的文件时更安全。
- v2.0.3 的假样本数不填时按 `文件大小 / 6400` 计算（约 0.125%，取自参考样本）。
- v0.4 的标签文字默认是 `Patched By ShoreUploader`。
- v0.4 默认按站点逻辑先预处理（需要本机有 `ffmpeg`）：平均帧率 ≥119.995 时重编码到 119.97 fps，否则音频不是 48 kHz AAC 时转成 48 kHz AAC。`--no-preprocess` 跳过这两步、只跑 patcher；`--ffmpeg` 指定 ffmpeg 路径。

## 各版本改了什么

### v2.0.0：无限时长 + 元数据打标

只改容器元数据，不动任何样本表：

1. `mvhd` 重写为 version 1，创建/修改时间清零，`duration = 0xFFFFFFFFFFFFFFFF`
2. 所有 `tkhd` / `mdhd` 的创建、修改时间清零，真实时长保留
3. 视频 `avc1`（及 `avc3`/`hvc1`/`hev1`）的 compressorname 改写为 `ShoreUploader-v2.0.0`
4. `moov` 下的 `udta` 替换为固定标签：`©ART`/`©nam`/`©wrt` = `ShoreUploader`，`©too`/`©enc`/`©cmt`/`cprt` = `ShoreUploader-v2.0.0`
5. `moov` 变大后，所有 `stco`/`co64` 偏移整体平移
6. 输出固定为 `ftyp + moov + mdat`，不追加任何字节

### v2.0.3：视频样本表注入空壳

1. `mvhd` / `tkhd` / `mdhd` 的创建、修改时间清零，保留真实时长
2. 视频轨 `hdlr` 名改写为 `shoreuploader-coded`
3. `avc1` 的 compressorname 改写为 `ShoreUploader-v2.0.3`
4. `moov` 下的 `udta` 替换为固定标签（`©too` / `©cmt` / `cprt`）
5. 视频 `stsz` 尾部追加 N 个大小为 8 的样本，`stts` 保持不变
6. `stco` 追加 N 个 chunk，偏移全部指向文件末尾的 8 字节桩
7. `stsc` 追加一条尾条目（samples_per_chunk = 1）覆盖这些 chunk
8. `mdat` 尾部追加 8 字节 `00 00 00 04 00 00 00 00`

解码器按顺序播放真实样本，遇到尾部空壳时报 `missing picture in access unit with size 8`，画面不受影响。

### v0.4：音频轨空壳 + 克隆音频轨

这是 shoreuploader.com 当前页面（页脚 v0.4）实际下发的算法，从 `/api/patcher` 的混淆代码还原：

1. 视频轨：用 `stts` 重算 `mdhd` 时长 K；`tkhd` 时长按电影时间基准换算；`elst` 重置为单条 `(时长, 0, 1.0)`；没有 `pasp` 但有 `btrt` 时插入 1:1 的 `pasp`
2. 音频轨：`stsz` 追加 18 个样本（大小 `[23] + 17×[6]`）；`stts` 第一条的 count 改成新总数；`stsc` 重写为 `[[1,1,1],[M,1,2]]`（`M = max(1, 总数-7)`）；`stsd` 换成固定的双 `mp4a` 模板（48 kHz）
3. 克隆音频轨：`track_ID = 3`，`stsd` 换成固定的单 `mp4a` 模板，`stsc = [[1,1,1]]`，`stco`/`stsz`/`stts` 与原音频轨共用同一份数据
4. `mvhd` 时长取视频/音频的较大值，`next_track_ID = 4`
5. `udta` 替换为 `©too = Lavf62.3.100`、`©ART`/`©cmt = Patched By ShoreUploader`
6. 输出为 `ftyp + moov + 8 字节 free + mdat + 125 字节尾巴`；尾巴是 `de02...Lavc62.11.100...` 加 17 个 6 字节标记，18 个假音频样本的 `stco` 偏移依次指向它

### 站点侧预处理（脚本已内置）

v0.4 的固定 `stsd` 模板和 `样本数 × 1024` 的时长算法要求音频是 48 kHz AAC，平台又只接受 ≤120 fps 的透传文件，所以站点在调用 patcher 之前会按下面的顺序处理，脚本默认执行同样的步骤：

1. **平均帧率 ≥119.995**（`stsz 样本数 × mdhd timescale / stts 总时长`）：用
   `ffmpeg -r 11997/100 -map 0:v:0 -map 0:a:0? -c:v libx264 -preset veryfast -crf 17 -c:a aac -b:a 192k -ar 48000 -movie_timescale 1000 -video_track_timescale 119970`
   重编码，音频同时变成 48 kHz AAC。
2. **否则**，若第一条音轨不是 `mp4a` + esds（objectTypeIndication `0x40`），或 `mdhd` timescale 不是 48000：用
   `ffmpeg -map 0:v:0 -map 0:a:0? -c:v copy -c:a aac -b:a 192k -ar 48000 -movie_timescale 1000`
   只转音频，视频流原样复制。
3. 最后才调用 patcher。

站点还会在预处理前硬性拒绝 >4K（3840×2160）和 ≥120.01 fps 的文件；脚本不做这两项拒绝，而是按上面的规则尽量处理。`--no-preprocess` 可以跳过预处理，此时输入必须自己保证是 48 kHz AAC，否则音轨会“声明 48k、实际不是”。

## 验证

- **v2.0.0**：用 46,143,702 字节的 v2.0.0 样本（1000×2168、60 fps、25.3 s、AAC 48k）重跑，输出与原文件**逐字节相同**（MD5 `d8370974bae3b4da8b3620cf5f68c1ce`）。
- **v0.4**：把 shoreuploader.com 的原始混淆 patcher 放进无头 Edge 里实际执行，与 `shoreuploader_v04.py --no-preprocess` 的输出对比，在 5 个输入上全部**逐字节相同**：
  - 3 个 ffmpeg 合成样本（含 moov 在尾、44.1 kHz、单声道、带 `pasp` 的情况）
  - 2 个真实样本（v2.0.0 的 46 MB 文件、v2.0.3 的 58 MB 文件）
- 预处理按站点的参数执行，但本机 ffmpeg 与站点的 ffmpeg.wasm 版本不同，重编码后的文件不会与站点逐字节相同；只要结构满足（48 kHz AAC、单一 `stts`），patcher 阶段的输出仍与站点一致。
- **v2.0.3**：见下方实测表。

## 实测结果（2026-11，抖音）

| 输入规格 | 上传大小 | 服务端下发 | 结果 |
|---|---|---|---|
| 1080×1920，60fps，25 Mbps | 58,318,271 B | 58,318,271 B | 原样透传 |
| 2160×2160，20fps，15 Mbps | 26,896,214 B | 26,896,214 B | 原样透传 |
| 720×720，60fps，2.4 Mbps | 5,464,935 B | 2,679,037 B | 被转码成 720p30 |

结论只代表当时的平台行为：高分辨率、高码率的源文件更容易命中保留原文件的路径，低码率小文件会被正常转码。

## 限制

- **v2.0.0**：只支持 32 位 `stsz` 和 `stco`/`co64`；输出会丢掉 `ftyp`/`moov`/`mdat` 之外的顶层 box。
- **v2.0.3**：只处理 `stco`。文件超过 4 GB 需要 `co64`，脚本会直接报错。只支持可变大小的 `stsz`（sample_size = 0）。**输入必须是干净文件**：`stsz` 样本数等于 `stts` 计时样本数；对已经打过补丁的文件再跑会破坏样本表。
- **v0.4**：只保留第一条视频轨、第一条音频轨和克隆轨，其余 `trak` 会被丢弃；只处理 32 位 `stco`；没有音频轨时只保留视频轨、不追加尾巴。它生成的音频 `stsd` 是固定模板，ffmpeg 解码该轨时会报声道布局不支持，但容器结构本身与站点输出一致。预处理需要本机有 `ffmpeg`（或 `--ffmpeg PATH`），否则需要 `--no-preprocess` 并自行保证 48 kHz AAC。站点的检查只看编码类型和采样率、不看声道数和 AAC profile，所以单声道或 HE-AAC 源仍可能通过检查，却与模板声明的双声道 AAC-LC 不一致。
- 三个版本都不要对输出使用 `ffmpeg -c copy` 重封装，空壳样本会被物化成真实样本。
- 这些改写都不符合严格的 MP4 规范，效果依赖平台实现，可能随时失效。

## 免责声明

仅供学习、格式研究和自己在自己设备上的文件使用。使用本工具可能违反相关平台的服务条款，由此产生的一切后果由使用者自行承担。

请勿用它把他人作品当作自己的内容发布。

## License

MIT，见 [LICENSE](LICENSE)。
