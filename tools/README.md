# tools

只读取证脚本，配合 [`../DOUYIN_TRANSCODE_NOTES.md`](../DOUYIN_TRANSCODE_NOTES.md) 使用。全部只依赖 Python 标准库，不联网。

## 安全

下载的 JSON / MP4 是不可信数据：

- 每个样本放在自己的新空目录里；
- 脚本放在另一个目录（本仓库的 `tools/`），不要从下载目录运行；
- 用 `python -I` 运行，避免解释器加载下载目录里的 `json.py`、`sitecustomize.py` 等；
- 不要把下载目录加入 `PYTHONPATH`。

## 用法

```bash
python -I tools/parse_gear.py <detail.json>
python -I tools/parse_meta.py <detail.json>
python -I tools/probe_tables.py <file.mp4>
```

- `parse_gear.py`：打印 `aweme_detail.video` 的分辨率、时长、默认 `play_addr`，以及 `bit_rate[]` 每一档的 `gear_name` / `quality_type` / 码率 / FPS / H.265 / 分辨率 / `data_size`。
- `parse_meta.py`：`video.meta` 是 JSON **字符串**，这个脚本原样打印前 4000 字符，并打印 `audio` / `bit_rate_audio`。
- `probe_tables.py`：打印顶层 box、`mvhd`、每个 `trak` 的 `tkhd`/`mdhd`/`elst`/`stts`/`stsz`/`stsc`/`stco`，以及按 `stsc` 推导的 chunk/样本映射、最大样本结束偏移、尾部样本大小。用来找 ghost samples、重复音轨、EOF 桩和样本表不一致。

## 输入格式

- `parse_gear.py` / `parse_meta.py` 假设输入是 `aweme/detail` 的原始 JSON 对象，顶层有 `aweme_detail`。如果抓到的是数组或包了别的字段，先自己解包。
- `probe_tables.py` 只支持普通 MP4（`ftyp`/`moov`/`mdat`），不支持分片 MP4（`moof`/`sidx`）和加密样本。
