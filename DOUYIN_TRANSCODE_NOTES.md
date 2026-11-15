# 抖音透传 / 转码研究笔记

> 2026-11-15 · 阶段性记录
>
> 配套代码：`shoreuploader_v200.py`、`shoreuploader_v203.py`、`shoreuploader_v04.py`；算法细节见 `README.md`。
>
> 本文件记录线上取证、推断和下一轮实验。**推断不是结论**，每条都标了证据等级：
>
> - ✅ **已验证**：API 返回或文件级取证直接支持。
> - 🟡 **推断**：与现有数据一致，但存在混杂变量，不能单独证明因果。
> - 🔬 **待验证**：下一轮实验要证实/证伪的点。

---

## 0. TL;DR

1. ✅ v2.0.3 的 ghost 样本数约为 `文件大小 / 6400`；真实视频样本数约为 `fps × 时长`。因此 ghost 密度近似为：
   `G/N ≈ 码率 / (51,200 × fps)`。
   30fps 比 60fps 有利 2 倍，比 120fps 有利 4 倍。
2. 🟡 线上样本支持“ghost 密度阈值大约在 2–6”的假设：1080p60 25 Mbps（密度约 8.1）和 2160×2160 20fps 15 Mbps（约 14.6）是 original-only；720p60 2.4 Mbps（约 0.8）和 720p20（约 1.4–2.5）被转码。
3. ✅ 但 4K120 案例说明 **ghost 密度不是唯一变量**：平台对 120fps 有独立的 30fps 输出模板。该视频 14 档全部 30fps，最高 4K 档是 `adapt_lowest_4_1`（3.79 Mbps），没有 `original`。它“没限流”不等于“4K120 透传”。
4. 🟡 **original-only 不一定是好事**：没有 ABR 梯子，重流起播慢、弱网卡顿，会表现成“限流假象”。4K120 被平台转成轻量 14 档梯子后反而传播好。
5. ✅ v0.4 是固定剂量：18 个音频空壳 + 克隆音轨 + 约 0.4 秒静音尾；不随文件大小缩放。它适合做机制对照，不适合按码率调剂量。
6. 🟡 v2.0.3 的 ghost 数默认 `size/6400`，但移植的站点引擎是固定 9112；其他文件大小用哪个规则尚未验证。
7. ✅ `ffmpeg -itsscale 2 -i in -c:v copy -c:a copy out` 是真实的时间戳缩放，不是结构绕过：2 秒 / 30fps → 3.97 秒 / 15fps，播放 0.5×；`-itsscale 0.5` → 1.02 秒 / 60fps，播放 2×。它不产生 ghost/重复音轨，容器合法，但会改播放速度、可能音画不同步，而且不改 H.264 SPS VUI。不是通用透传。
8. 🔬 下一轮优先级：**1080p30 8 Mbps + v2.0.3**（密度 5.2）> 720p30 8 Mbps（隔离分辨率）> 1080p60 8 Mbps（隔离 fps）> 4/6/10 Mbps 阈值扫描 > 干净对照。

---

## 1. 仓库里有什么

| 脚本 | 对应版本 | 核心机制 | 输出指纹 |
|---|---|---|---|
| `shoreuploader_v200.py` | v2.0.0 | `mvhd` v1 + 无限时长，元数据打标 | `ShoreUploader-v2.0.0` |
| `shoreuploader_v203.py` | v2.0.3 | 视频 `stsz`/`stco`/`stsc` 注入 8 字节空壳样本 | `ShoreUploader-v2.0.3`、`shoreuploader-coded` |
| `shoreuploader_v04.py` | v0.4 | 音频轨 18 个空壳 + 克隆音频轨 + 125 字节尾 | `Lavf62.3.100`、`Patched By ShoreUploader` |

三个脚本都只改容器、不动 `mdat` 里的音视频数据。`README.md` 里有逐字段的算法说明和逐字节验证结果。

**已验证的本地样本**（不随仓库提交，`.gitignore` 忽略 `*.mp4`）：

| 样本 | 大小 | 版本 | 结果 |
|---|---:|---|---|
| `riatsuae_..._1792595559739.mp4` | 58,318,271 B | v2.0.3 | 服务端原样返回 |
| `izuru.01_..._1794499124975.mp4` | 69,353,932 B | v2.0.0 | 服务端原样返回 |
| `izuru.01_..._1794501320510.mp4` | 75,180,657 B | v0.4 | 服务端原样返回 |
| `siesta.ae_..._1792595363713.mp4` | 46,143,702 B | v2.0.0 | 服务端原样返回 |

这些文件的 `©too`、`hdlr`、compressorname、重复音轨、尾部空壳都还在，说明平台确实把上传件原字节下发，而不是“补丁后又被轻量封装”。

---

## 2. 线上档位数据

### 2.1 关键样本

| 视频 | 源规格 | 服务端结果 | 结论 |
|---|---|---|---|
| `8sENkfVyYO0` / `7693545133744164138` | 2160×4764，26.45 s，H.265，60 fps，94.04 Mbps，310,929,890 B | 仅 `original_2160_1`，q=10000，`vqs_origin="0"`，`sdgs=["original_2160_1"]` | ✅ original-only；重流，播放卡 |
| `KsQJWZJZR5A` / `7701552431557053739` | 1724×3840，22.549 s，H.264，`FPS=281`，19.00 Mbps，53,568,627 B | 仅 `original_2160_0`，q=10000，无 `vqs_origin`，`sdgs=["original_2160_0"]` | ✅ original-only；高 fps 源也保留 |
| `6truAkJGg1k` / `7705647963653115155` | 720×720，17.067 s，20 fps | 7 档全部 30 fps，最高 `normal_720_0` 1,421,233 bps / 3,032,025 B；无 1080p；`vqs_origin=66.59`；`sdgs` 全 7 档 | ✅ 被转码；v2.0.3，ghost 剂量不足或模板不匹配 |
| `-dmzZABE_Dk` / `7687491288509222190` | 3840×2160，38.534 s，声称 4K120 | 14 档全部 30 fps，最高 4K 档 `adapt_lowest_4_1` 3.79 Mbps；无 `original`；默认 1080p 3.04 Mbps | ✅ 标准转码，不是 4K120 透传；详见第 4 节 |
| `7704132315496058121` | 公开视频 | 8 档，最高 720p 2,781,702 bps，540p 多档，H.265 adapt，`vqs_origin=55.82`，`is_source_HDR=1` | ✅ 普通多档梯子 |

README 里的三个实测：

| 输入 | 上传大小 | 服务端下发 | 结果 |
|---|---:|---:|---|
| 1080×1920，60fps，25 Mbps，v2.0.3 | 58,318,271 B | 58,318,271 B | ✅ 原样透传 |
| 2160×2160，20fps，15 Mbps，v2.0.3 | 26,896,214 B | 26,896,214 B | ✅ 原样透传 |
| 720×720，60fps，2.4 Mbps，v2.0.3 | 5,464,935 B | 2,679,037 B | ✅ 被转码成 720p30 |

注意：720p20 的 `6truAkJGg1k` 和 README 里的 720p60 2.4 Mbps 是**两个不同样本**，不要混在一起。

### 2.2 gear 命名与 `quality_type`

从 10 个相关视频的 153 条 gear 记录里归纳（🟡 样本仍有限）：

| gear 前缀 | `quality_type` | 含义 |
|---|---:|---|
| `original_*` | 10000 | 原文件，最高优先级 |
| `normal_1080_0` | 1 | 正常 1080p 档 |
| `1080_1_1` | 3 | 1080p 备选档（常见 H.265） |
| `adapt_lowest_1440_1` | 7 | 1440p 自适应低档 |
| `normal_720_0` | 10 | 正常 720p 档 |
| `720_1_1` … `720_4_1` | 11–14 | 720p 备选档 |
| `normal_540_0` | 20 | 正常 540p 档 |
| `adapt_lowest_4_1` | 72 | 4K 自适应低档（不是 normal/original） |
| `low_720_0` | 211 | 低优先级 720p |
| `adapt_low_540_0` | 291 | 自适应低 540p |
| `low_540_0` | 292 | 低优先级 540p |

q 值不是按分辨率单调递增的；`adapt_lowest_4_1` 的 q=72 只表示它是 4K 的“自适应低档”，不代表画质高于 `normal_1080_0`（q=1）。

### 2.3 `meta` 字段

`aweme_detail.video.meta` 是 **JSON 字符串**，不是对象。常用字段：

- `sdgs`：本次实际生成的 gear 列表，JSON 字符串。
- `enable_manual_ladder`：是否手动/自定义梯子。
- `gear_vqm`：如 `{"1080p_720p":2,"720p_540p":-1}`，表示档位间的 VQM 参考。
- `vqs_origin`：源质量分；`72.14` 属于较高，`66.59` 中等，`55.82` 偏低，`"0"` 是异常/未算。
- `sr_score` / `sr_potential` / `qprf`：超分/质量相关分数。
- `strategy_tokens`：如 `nosla01_0818`、`model01_0919`、`remove_vqe4lite`。
- `loudness` / `volume_info`：响度。
- `is_source_HDR`：源是否 HDR（有时缺失）。

---

## 3. 平台行为模型

### 3.1 三道闸门

🟡 这是目前最顺的解释，不是平台文档：

1. **内容/规格模板**
   - 30fps 输出模板：120fps 源会被转成 30fps 梯子（4K120 案例）。
   - 站点侧 v0.4 预处理：平均帧率 ≥119.995 时重编码到 119.97；≥120.01 直接拒绝；>4K 拒绝。
   - 20fps 源不一定被强制转码：2160×2160 20fps 15 Mbps 的 original-only 样本说明“20fps”本身不是死刑，关键看模板和补丁剂量。
2. **输入/容器校验**
   - 样本表一致性：`stsz` 样本数 vs `stts` 计时样本数、`stco` 偏移、`stsc` 覆盖、`elst` 与 `tkhd/mdhd` 时长。
   - 补丁指纹：ghost 样本、重复音轨、`hdlr`/compressorname/`©too`。
   - 文件大小/码率/分辨率上限；v2.0.3 只支持 32 位 `stco`，>4 GB 会失败。
3. **分发/播放**
   - 保留原文件 → `original` 单档，没有 ABR 梯子。
   - 转码 → 多档梯子，弱网/低端设备有降级空间。
   - “限流”体感往往来自第 3 层：original-only 重流起播慢、卡顿、完播率低；不一定是平台主动压流量。

### 3.2 original-only 的两面性

✅ 文件级事实：original-only 意味着平台把上传件原字节下发，画质最好。

🟡 行为推断：

- 没有 ABR 梯子，播放器只能拉原始码率；弱网/低端设备容易缓冲。
- 冷启动要等更多数据，完播率/互动可能变差。
- 对高码率 4K60/4K120，original-only 可能“画质最好但传播最差”。
- 平台转码成多档梯子反而可能提升传播；4K120 案例的 14 档轻量梯子 + 33k 点赞/7k 分享就是一个例子（但不能证明因果）。

**结论：目标不是“无论如何 original-only”，而是“画质 + 可流式播放”的平衡。** 1080p30 6–10 Mbps 仍然是最值得先试的区间；4K120 不是。

### 3.3 ghost 比例模型

✅ 公式来自 v2.0.3 的实现：

```text
G ≈ 文件大小 / 6400
N ≈ fps × 时长
文件大小 ≈ 码率 × 时长 / 8
G/N ≈ 码率 / (51,200 × fps)
```

🟡 用现有样本估算（源文件大小/码率不确定的标“约”）：

| 场景 | 码率 | fps | G/N 估算 | 线上结果 |
|---|---:|---:|---:|---|
| 1080p30 8 Mbps | 8 Mbps | 30 | **5.21** | 待测（优先级 1） |
| 720p30 8 Mbps | 8 Mbps | 30 | **5.21** | 待测（隔离分辨率） |
| 1080p30 6 Mbps | 6 Mbps | 30 | 3.91 | 待测 |
| 1080p30 10 Mbps | 10 Mbps | 30 | 6.51 | 待测 |
| 1080p60 8 Mbps | 8 Mbps | 60 | 2.60 | 待测（隔离 fps） |
| 1080p60 25 Mbps | 25 Mbps | 60 | **8.14** | ✅ original-only |
| 2160×2160 20fps 15 Mbps | 15 Mbps | 20 | **14.65** | ✅ original-only |
| 720p60 2.4 Mbps | 2.4 Mbps | 60 | **0.78** | ✅ 转码 720p30 |
| 720p20（源码率未知） | 1.4–2.6 Mbps | 20 | 约 **1.35–2.5** | ✅ 转码 7 档 |
| 4K120 25 Mbps | 25 Mbps | 120 | 约 **4.07** | 若源约 25 Mbps，落在 2–6 区间内但仍被转码 |
| 4K120 94 Mbps | 94 Mbps | 120 | 约 **15.30** | 若源约 94 Mbps，密度够高但仍被转码 |
| 4K60 94 Mbps | 94 Mbps | 60 | 约 30.60 | 4K60 原始样本 original-only |

🟡 推断：

- 通过区间大约在 **2–6**，但 720p20（约 1.4–2.5）和 4K120（若约 4.1）都在区间内或附近却失败，说明还有分辨率/fps 模板等独立闸门。
- **30fps 是最优点**：同样码率下，ghost 密度是 60fps 的 2 倍、120fps 的 4 倍；而且 30fps 的输出模板最稳。
- **4K120 是最差点**：要达到 30fps 的 ghost 密度，码率得是 4 倍；4K120 25 Mbps 只有 4.07，4K120 94 Mbps 虽然到 15.3，但文件巨大、original-only 也难流式播放。
- 720p20 失败不一定是“ghost 不够”单独造成；它同时低于 720p/30fps 的输出模板。**必须用 720p30 8 Mbps 和 1080p30 8 Mbps 做对照**，才能把分辨率、fps、ghost 密度分开。

---

## 4. 4K120 案例完整取证

链接：`https://v.douyin.com/-dmzZABE_Dk/`  
`aweme_id`：`7687491288509222190`  
描述：`在雨后醒来. #cs2`  
创建时间：2026-09-20 13:52:29

### 4.1 API 层

✅ `video`：

- `width=3840`，`height=2160`，`duration=38534 ms`
- `ratio=1080p`，`format=mp4`，`is_h265=0`
- 默认 `play_addr`：1920×1080，14,644,487 B
- `play_addr_h264`：14,644,487 B
- `play_addr_265`：11,054,534 B
- `download_addr`：None

✅ `bit_rate[]`：**14 档，全部 `FPS=30`，没有 `original_*`**。

| # | gear | q | 编码 | 分辨率 | fps | 码率 | 大小 |
|---:|---|---:|---|---|---:|---:|---:|
| 1 | `adapt_lowest_4_1` | 72 | H.265 | 3840×2160 | 30 | 3,794,624 | 18,277,757 |
| 2 | `normal_1080_0` | 1 | H.264 | 1920×1080 | 30 | 3,040,325 | 14,644,487 |
| 3 | `adapt_lowest_1440_1` | 7 | H.265 | 2560×1440 | 30 | 2,295,019 | 11,054,534 |
| 4 | `normal_540_0` | 20 | H.264 | 1024×576 | 30 | 2,145,577 | 10,334,709 |
| 5 | `normal_720_0` | 10 | H.264 | 1280×720 | 30 | 2,036,698 | 9,810,266 |
| 6 | `low_720_0` | 211 | H.264 | 1280×720 | 30 | 2,016,261 | 9,711,826 |
| 7 | `low_540_0` | 292 | H.264 | 1024×576 | 30 | 1,766,322 | 8,507,932 |
| 8 | `1080_1_1` | 3 | H.265 | 1920×1080 | 30 | 1,678,833 | 8,086,523 |
| 9 | `lower_540_0` | 224 | H.264 | 1024×576 | 30 | 1,420,668 | 6,843,006 |
| 10 | `720_1_1` | 11 | H.265 | 1280×720 | 30 | 1,166,342 | 5,617,980 |
| 11 | `adapt_low_540_0` | 291 | H.264 | 1024×576 | 30 | 1,138,453 | 5,483,646 |
| 12 | `720_2_1` | 12 | H.265 | 1280×720 | 30 | 802,815 | 3,866,962 |
| 13 | `720_3_1` | 13 | H.265 | 1280×720 | 30 | 555,367 | 2,675,065 |
| 14 | `720_4_1` | 14 | H.265 | 1280×720 | 30 | 532,223 | 2,563,587 |

✅ `meta` 关键字段：

- `sdgs`：全部 14 档。
- `enable_manual_ladder=1`
- `vqs_origin=72.14`（源质量较高）
- `gear_vqm={"1080p_720p":2,"720p_540p":-1}`
- `sr_score=1.000`，`qprf=1.000`，`sr_potential={"v1.0":{"score":43.094}}`
- `strategy_tokens=["online","reward02_0810","reward01_0810","model01_0919","model02_0919","remove_vqe4lite"]`
- `audio_bitrate_set=["128k_aac_lc","32k_aac_he_v2","64k_aac_he_v2","64k_unknown"]`
- `audio_bitrate_values=[24182,24642,24681,31270,32347,64192]`
- `loudness=-14.6`，`is_spatial_video=0`，`is_source_HDR` 缺失。

✅ 互动数据（抓取时）：

- `digg_count=33,368`
- `share_count=7,284`
- `collect_count=1,149`
- `comment_count=387`
- `recommend_count=1,723`
- `play_count=0`（Web API 隐藏）

### 4.2 下载 4K 档验证

✅ 通过 `www.douyin.com/aweme/v1/play/` 的签名接口下载 `adapt_lowest_4_1`，`Content-Length` 18,277,757，与 `data_size` 一致。

`ffprobe`：

- 视频：HEVC Main，3840×2160，**30fps**，1156 帧，38.533333 s，3,765,792 bps
- 音频：AAC HE-AACv2，44.1 kHz，2 声道，38.498005 s，24,182 bps
- 容器：38.533333 s，3,794,690 bps

样本表：

- `ftyp` 28 B，`moov` 22,768 B，`free` 8 B，`mdat` 18,254,953 B
- `mvhd` v0，timescale 1000，duration 38,534
- 视频 `mdhd` timescale 15360，duration 591,872；`tkhd` 38,534；`elst` `(38534, 2048)`
- 视频 `stts` 只有一条 `(1156, 512)` → 512/15360 = 1/30 s
- 视频 `stsz` 1156，`stsc` 180 条，`stco` 417 个 chunk，`sum sizes=18,138,569`
- **没有 ghost samples，`samples beyond stts=0`**
- 音频 `stsz` 419，`stts` 419，`stsc` 2 条，`stco` 418 个 chunk；**只有一条音轨，没有克隆轨**
- `max end=18,277,633`，文件 18,277,757，尾部 124 B 是正常音频样本/填充

✅ 标签：

- `encoder=Lavf58.76.100`
- `comment=vid:v0d00fg10000dann7qnog65rccpfoaug`
- `handler_name=VideoHandler` / `SoundHandler`
- `language=und`
- `major_brand=isom`，`minor_version=512`，`compatible_brands=isomiso2mp41`

### 4.3 这说明什么

✅ **服务端不是 4K120 透传。** 它是 4K30 的标准平台转码：

- 14 档全部 30fps；
- 最高 4K 档是 `adapt_lowest_4_1`，不是 `original_2160_*`；
- 4K 档 3.79 Mbps，1080p 默认档 3.04 Mbps，4K 档只是“自适应最低 4K”，不是高质量 4K；
- 容器是 `Lavf58.76.100`，有平台 `comment=vid:...`，handler 是 `VideoHandler/SoundHandler`；
- 没有 ShoreUploader/NoBlur/tiktok-lossless 的任何指纹。

✅ **“没限流”在传播意义上可能是真的**：33k 点赞、7k 分享、1.1k 收藏。但这是内容 + 轻量梯子的结果，不是 4K120 透传的证据。

🟡 **上传方法无法从成品反推**：平台已经重编码，上传侧标签被洗掉。可能的解释：

1. **直接上传 4K120 源**，平台按 30fps 模板转成 4K30 的 14 档梯子；没有用绕过方法。这是最简单的解释。
2. 用了 v2.0.3/v0.4/NoBlur，但**没生效**，平台照样转码；补丁指纹被平台转码覆盖。
3. 用了 `-itsscale 4` 把 9.6 s / 120fps 拉成 38.5 s / 30fps 声明；平台再转码。成品同样是 1156 帧 / 38.5 s，但内容是 0.25× 慢动作。单看帧数无法和 1 区分。
4. 用了 FPS bypasser（只改 `mvhd/mdhd`），平台仍按实际码流/模板转码；成品无痕迹。

🔬 要确认方法，必须拿到**上传前的源文件**和上传侧 `ffprobe`：

- `r_frame_rate` / `avg_frame_rate` / `duration`
- `stts` 总时长和 `stsz` 样本数
- `©too`、`hdlr` 名、compressorname
- ghost 样本数、是否有重复音轨、尾部是否有静音/空壳
- 本地文件大小 / MD5

如果源是 4K120、38.5 s，样本数约 4620；平台 4K 档只有 1156 帧，正好是 1/4，符合“120→30 丢 3/4 帧”。如果源是 9.6 s、120fps（1156 帧）再用 `-itsscale 4` 拉长，成品也会是 1156 帧 / 38.5 s，但内容是慢动作。**两者从成品帧数无法区分。**

### 4.4 对 4K120 的判断

✅ 4K 分辨率是真的（3840×2160）。

✅ 120fps 在服务端不是真的：所有档位 30fps，4K 档 30fps，没有 120fps 档。

🟡 4K120 是 ghost 比例模型里最差的目标：

- 同样码率下，ghost 密度只有 30fps 的 1/4；
- 4K120 25 Mbps → 约 4.07，落在推断的 2–6 区间内，却仍被转码 → 说明 30fps 模板是独立闸门；
- 4K120 94 Mbps → 约 15.3，密度够高，但文件巨大，original-only 也难流式播放；
- 平台的 30fps 输出模板是独立闸门，ghost 密度再高也可能被转码。

**结论：4K120 不适合作为“透传 + 流量”的主路径。** 如果目标是画质 + 传播，1080p30 6–10 Mbps 仍然优先；4K120 大概率被转成 4K30 低码率档。

---

## 5. `ffmpeg -itsscale` 实测

### 5.1 命令与语义

```bash
ffmpeg -itsscale 2 -i input.mp4 -c:v copy -c:a copy output.mp4
```

- `-itsscale` 是**输入选项**，把输入时间戳乘以给定因子。
- `-c:v copy -c:a copy` 只重封装，不重编码；样本字节不变。
- 输出会重写 `mvhd/mdhd/tkhd/elst/stts` 等时间字段，重新计算 `stco`，但不会产生 ghost 样本、重复音轨或非法 chunk。

### 5.2 本地实测

测试源：2.000 s、320×240、30fps、H.264 + AAC。

| 命令 | 容器时长 | 视频时长 | 音频时长 | `r_frame_rate` | 播放速度 |
|---|---:|---:|---:|---:|---|
| 原文件 | 2.000 s | 2.000 s | 2.000 s | 30/1 | 1× |
| `-itsscale 2` | 3.9969 s | 3.9667 s | 3.9969 s | 15/1 | **0.5× 慢动作** |
| `-itsscale 0.5` | 1.0167 s | 1.0167 s | 1.0101 s | 60/1 | **2× 快放** |

`-itsscale 2` 的 `avg_frame_rate=1800/119`（约 15.13），`-itsscale 0.5` 的 `avg_frame_rate=3600/61`（约 59.02）。音视频时长差约 30 ms（4 秒测试），更长视频可能累积。

### 5.3 它改了什么、没改什么

✅ 改了：

- 容器时间戳/时长：`mvhd/mdhd/tkhd/elst/stts` 全部按因子缩放；
- 声明帧率：样本数不变、时长变 → 帧率反向变化；
- 声明平均码率：文件大小不变、时长变 → 码率反向变化。

✅ 没改：

- H.264/HEVC 的样本字节；
- H.264 SPS VUI 里的 timing info（`-c copy` 不碰码流）；
- AAC 的 sample rate / 编码参数；
- 不产生 ghost 样本、不克隆音轨、不追加尾部空壳。

⚠️ 结果：

- 容器说 30fps，码流 VUI 可能还说 60fps；不同探测器可能读到不同值。
- 播放器按容器时间戳播放 → 0.5× 慢动作；音频也可能被拉慢/降调。
- `-c:a copy` 的 AAC 帧时长被缩放后不一定符合标准帧长，长视频可能音画漂移。

### 5.4 为什么博主会说“直接就行”

🟡 可能的情况：

1. **源文件时间戳本来就是错的**（例如 2× 快的文件），`-itsscale 2` 只是把播放速度修回正常；这是“修文件”，不是“绕过”。
2. 内容本来就是慢动作/幻灯片，0.5× 看不出来；平台没转码，他以为方法成功了。
3. 平台某条规则只看容器声明 fps/时长/码率，`-itsscale` 刚好越过阈值；换分辨率/码率就失效。
4. 纯口嗨/引流，没有对照 `sdgs`/`vqs_origin`。

### 5.5 结论

✅ 命令是真的，ffmpeg 确实这么工作。

❌ 它不是 ShoreUploader 那种结构绕过，也不是通用透传：

- 正常内容会变慢动作；
- 可能音画不同步；
- 码流 VUI 与容器时间戳不一致；
- 不解决分辨率/真实码率上限；
- 不产生 ghost/重复音轨，对“样本表密度”类闸门无效。

**如果要试，按这个做：**

1. 正常速度的 1080p30 8 Mbps 源，跑 `-itsscale 2`，先 `ffprobe` 再播放；如果是慢动作，就不能用于正常内容。
2. 60fps 源跑 `-itsscale 2`，上传后查 `bit_rate[]` 的 `gear_name`、`sdgs`、`vqs_origin`、`play_addr.data_size`；如果还是多档/被转码，说明没绕过；如果 original-only 但慢动作，那是“用速度换透传”。
3. 对照组：`-itsscale 1.0`（几乎无变化）和 `-itsscale 1.01`（只膨胀 1%）。如果 1% 也能改变结果，说明是阈值规则；如果只有 2× 有效，说明是帧率/时长比例规则。
4. 同源对比 v2.0.3/v0.4，平台返回的 gear 列表才是 ground truth。

---

## 6. 其他方案对比

| 方案 | 核心手法 | 改什么 | 是否改播放速度 | 备注 |
|---|---|---|---|---|
| ShoreUploader v2.0.0 | `mvhd` v1 无限时长 + 元数据打标 | 容器头 | 否 | 老方法；可 original-only，但依赖平台规划器 |
| ShoreUploader v2.0.3 | 视频样本表注入 8 B 空壳 | `stsz/stco/stsc/stts/elst/udta` | 否 | 当前主力；ghost 数≈size/6400；只支持 32 位 `stco` |
| ShoreUploader v0.4 | 音频 18 个空壳 + 克隆音轨 + 125 B 尾 | 音频表/`stsd`/`trak` | 否 | 固定剂量；站点预处理要求 48 kHz AAC、≤4K、<120.01 fps |
| NoBlur | 样本表 10× 膨胀 + dummy sizes | `stts/stsz/stco/stsc` | 否 | 声称“高密度内容跳过激进重压缩”；无独立验证 |
| tiktok-lossless-upload | 静音 AAC 尾 + 重复音轨；`legacy` 10× 音频膨胀 | 音频表/`mvhd`/尾部 | 否 | 偏 TikTok；有 184,100 B 尾部、`©cmt` |
| TikTok-FPS-Compression-Bypasser | 按 `30/fps` 缩放 `mvhd/mdhd` 的 timescale+duration | 容器头 | 否（但声明 fps 变） | 不改样本时间戳，实际帧率可能没变；可信度存疑 |
| TikTok-prep | 重编码 H.264 Main/BT.709 + 假 SEI + `©too` JSON | 码流+容器 | 否（重编码） | 不是透传，是伪装 |
| `-itsscale` | 输入时间戳整体缩放 | 容器时间戳 | **是** | 0.5×/2×；不产生 ghost；窄场景 |
| 干净上传 | 不 patch | 无 | 否 | 对照组；平台按规格正常转码 |

DyQuality-Pro 是**播放侧** LSPosed 模块（hook `SimVideoUrlModel.getBitRate()` 重排候选），不是上传侧透传，不要混进来。

---

## 7. 下一轮实验矩阵

### 7.1 优先级

| # | 源规格 | 补丁 | ghost 密度估算 | 目的 | 预期/判据 |
|---:|---|---|---:|---|---|
| 1 | 1080p30，8 Mbps，H.264，48 kHz AAC，20 s | v2.0.3 | **5.21** | 验证通过阈值 | 若 original-only → 密度阈值约 5；若转码 → 阈值 >5 或有其他闸门 |
| 2 | 720p30，8 Mbps，同内容/同时长 | v2.0.3 | 5.21 | 隔离分辨率 | 与 #1 对比：同密度下分辨率是否影响 |
| 3 | 1080p60，8 Mbps，同内容/同时长 | v2.0.3 | 2.60 | 隔离 fps | 若 #1 过、#3 不过 → fps 影响密度/模板 |
| 4 | 1080p30，4 / 6 / 10 Mbps | v2.0.3 | 2.61 / 3.91 / 6.51 | 阈值扫描 | 找到 original-only 的最小码率 |
| 5 | 720p30，8 Mbps | v0.4 | 固定剂量 | 机制对照 | 比较 v0.4 与 v2.0.3 在同规格下的结果 |
| 6 | 720p20，同 720p20 源 | v0.4 | 固定剂量 | 版本/机制对照 | 复现 720p20 失败，判断是剂量还是模板 |
| 7 | 1080p30，8 Mbps | 干净上传 | — | 基线 | 平台正常转码的档位/大小 |
| 8 | 4K120，尽量小文件 | 干净上传 / v2.0.3 | 1.3–15.3（看码率） | 确认 30fps 模板 | 预期 14 档 30fps、无 original；不是透传目标 |
| 9 | 1080p30 8 Mbps | v2.0.3 + 显式 ghost 数 9112 vs size/6400 | 可变 | 验证 ghost 计数规则 | 看站点到底用哪个规则 |
| 10 | 1080p30 8 Mbps | `-itsscale 2` / `1.01` | — | 验证时间戳把戏 | 看播放速度、`sdgs`、音画同步 |

### 7.2 每条记录什么

上传后抓 `aweme_detail`，至少记录：

- `video.width` / `height` / `duration`
- `bit_rate[]` 每条的 `gear_name`、`quality_type`、`bit_rate`、`FPS`、`is_h265`、`play_addr.width/height/data_size`
- `meta.sdgs`、`meta.vqs_origin`、`meta.gear_vqm`、`meta.enable_manual_ladder`、`meta.sr_score`、`meta.strategy_tokens`
- 是否有 `original_*`；`original` 的 `data_size` 是否等于本地文件大小
- 下载 top/original 档，`ffprobe` + `tools/probe_tables.py`；比对 MD5/大小
- `statistics.digg_count` / `share_count` / `collect_count` / `comment_count`（播放量 Web API 常隐藏）

### 7.3 控制变量

- 同一内容、同一时长、同一音频、同一上传时间窗口；不要一条视频剪两半导致内容不同。
- 干净对照必须同规格；否则无法区分“补丁效果”和“源规格效果”。
- 新号/同设备/同网络；至少 2–3 条重复，避免单条波动。
- 观察 24–48 h；`play_count` 看不到时，用点赞/分享/完播（如果能拿到）做代理。
- 注意 Web detail 可能返回 `filter_reason: core_dep` 或隐藏 original；换 endpoint/App/多试几次。

---

## 8. 未解问题

1. 🔬 v2.0.3 的 ghost 数到底是 `size/6400` 还是固定 9112？站点对 18 MB、58 MB、100 MB 文件分别用多少？
2. 🔬 ghost 密度的精确阈值是多少？2–6 只是区间；#1/#4 能把它缩小。
3. 🔬 720p20 失败是 ghost 密度不足，还是 720p/30fps 模板不匹配？#2/#6 能分开。
4. 🔬 4K120 的 14 档 30fps 是硬上限，还是因为源码率/ghost 密度不够？需要一个 4K120 高码率 + 高 ghost 的对照。
5. 🔬 为什么 4K 档是 `adapt_lowest_4_1`，没有 `normal_2160`？是平台对 4K 源只给自适应低档，还是源码率/时长导致？
6. 🔬 60fps 可以 original-only（1080p60 25 Mbps 已验证）；120fps 是否永远不行？有没有 120fps original-only 的反例？
7. 🔬 `-itsscale` 能否骗过任何一道闸门？SPS VUI 与容器时间戳不一致时，平台读哪个？
8. 🔬 `-itsscale` + v2.0.3 能否组合？v2.0.3 要求干净输入；`-itsscale` 输出是干净的，但时间戳已变，ghost 数按文件大小算，值得一试。
9. 🔬 original-only 是否真的伤害传播？需要同内容 A/B：original-only vs 多档转码，控制账号/时间/内容。
10. 🔬 v0.4 在 720p30 8 Mbps 下能否 original-only？固定音频剂量是否足够？
11. 🔬 4K 源 + v2.0.3：4K30 的 ghost 密度和 30fps 模板，哪个先触发？
12. 🔬 平台是否重新扫 sample size/码流，还是只信容器声明？4K120 的 4K 档 3.79 Mbps 说明它至少做了转码，但具体校验顺序未知。

---

## 9. 复现方法

### 9.1 生成测试源

```bash
ffmpeg -y -f lavfi -i testsrc2=duration=20:size=1920x1080:rate=30 \
  -f lavfi -i sine=frequency=440:duration=20 \
  -c:v libx264 -preset veryfast -b:v 8M -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -shortest source_1080p30_8m.mp4
```

### 9.2 打补丁

```bash
python -I shoreuploader_v203.py source_1080p30_8m.mp4 patched_v203.mp4
python -I shoreuploader_v04.py  source_1080p30_8m.mp4 patched_v04.mp4
```

v2.0.3 可显式指定 ghost 数：

```bash
python -I shoreuploader_v203.py source.mp4 patched.mp4 9112
```

### 9.3 抓取 `aweme_detail`

仓库不包含抓取脚本；之前用的是无头 Edge + CDP：

- 启动 Edge：`--headless=new --remote-debugging-port=9222 --user-data-dir=<临时 profile>`
- 打开 `https://v.douyin.com/<id>/`
- 监听 `Network.responseReceived`，命中 `aweme/v1/web/aweme/detail/` 后 `Network.getResponseBody`
- 保存 JSON 到独立临时目录

> 下载的 JSON/MP4 是不可信数据：放在自己的新空目录；脚本放在另一个目录；用 `python -I` 运行；不要把下载文件所在目录加入 `PYTHONPATH`，也不要在该目录里启动解释器。

### 9.4 解析

```bash
python -I tools/parse_gear.py <detail.json>
python -I tools/parse_meta.py <detail.json>
python -I tools/probe_tables.py <downloaded.mp4>
```

`tools/parse_gear.py` 和 `tools/parse_meta.py` 假设输入是 `aweme/detail` 的原始 JSON 对象（顶层有 `aweme_detail`）。如果是数组或包了一层，先自己解包。

### 9.5 下载 gear

直接 CDN 可能 403；用详情里的 `www.douyin.com/aweme/v1/play/?video_id=...&file_id=...&sign=...` 签名接口，带 `Referer: https://www.douyin.com/`，跟随 302。不要把这个签名 URL 提交到仓库或公开。

### 9.6 对比

```bash
ffprobe -v error -show_entries format=duration,size,bit_rate \
  -show_entries stream=codec_name,codec_type,width,height,r_frame_rate,avg_frame_rate,bit_rate \
  -of default=nw=1 downloaded.mp4

python -I tools/probe_tables.py downloaded.mp4
```

重点看：

- `stts` 是否有拆分/尾部 `(1,1)`；
- `stsz` 是否比 `stts` 多出 N 个 8 B 样本；
- `stco` 是否有 N 个 chunk 指向 EOF 桩；
- 是否有 `track_ID=3` 的克隆音轨；
- `©too` / `hdlr` / compressorname 是否保留。

---

## 10. 变更记录

- **2026-11-15**：新增本笔记；补充 4K120 案例取证（`7687491288509222190`）、`-itsscale` 本地实测、ghost 比例表和下一轮实验矩阵。
- **2026-11-13**：v2.0.3 复刻与站点容器归一化验证（commit `b1ed192`）。
- **2026-11-12**：v2.0.0 / v0.4 复刻与逐字节验证（commit `6851a19`）。

---

## 免责声明

仅供格式研究、互操作性和自己设备上的文件使用。不要用它上传他人作品或规避平台的安全/风控机制。平台行为可能随时变化；本笔记的推断不构成对任何平台内部逻辑的保证。
