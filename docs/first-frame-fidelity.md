# 图生视频首帧保真度测量

`scripts/measure_first_frame_fidelity.py` 用参考图和生成视频作为输入，在本机提取视频第一个解码画面并计算差异指标。脚本只调用本机 FFmpeg，不上传或改写输入文件。

## 使用

本机需要安装包含 `ffmpeg` 和 `ffprobe` 的 FFmpeg：

```bash
python3 scripts/measure_first_frame_fidelity.py \
  "/path/to/reference.png" \
  "/path/to/generated.mp4" \
  --json-out "/path/to/first-frame-report.json" \
  --save-frame "/path/to/first-frame.png"
```

如果不传 `--json-out`，报告会打印到终端。若输出路径已存在，脚本会停止；确认要覆盖时添加 `--force`。

## 画幅匹配

比较前会把参考图和首帧映射到视频首帧的画幅。默认 `--fit contain`，保留参考图完整内容，并在空白处补黑边。若工作流会居中裁图，使用 `--fit cover`；若工作流会直接拉伸图像，使用 `--fit stretch`。对齐方式会写入报告。不同宽高比时，选错方式会显著改变分数。

默认比较画布长边最多为 1024 像素，低分辨率视频帧不会被放大。可以通过 `--max-side` 调整，取值范围为 16–2048。裁切和重采样使用 Lanczos。

## 指标

- `structure_difference_index_percent`：局部亮度 SSIM 的反向指数，按 `100 × (1 − clamp(SSIM, 0, 1))` 计算。值越大，局部结构差异越大。它不是主观画质百分比，也不是模型评分。
- `ssim_luma`：基于 BT.709 亮度、11×11 均匀局部窗口计算的结构相似度，接近 1 表示结构接近；画布短边小于 11 像素时，窗口会自动缩小为可用的最大奇数尺寸。
- `psnr_db`：RGB 像素误差的峰值信噪比，越高越接近。像素完全一致时 JSON 中为 `null`，并将 `perfect_pixel_match` 设为 `true`。
- `rgb_mae_0_255`：RGB 三通道的平均绝对误差，范围为 0–255。
- `pixels_over_error_threshold_ratio_percent`：平均 RGB 误差达到 8 位色阶 5%（12.75）以上的画布像素占比。
- `mean_signed_color_shift_output_minus_reference`：生成首帧减去参考图的通道平均偏移，可用于观察整体偏色。
- `aspect_ratio_delta_percent`：参考图与首帧宽高比的相对差异；它提醒你检查所选 fit 是否符合工作流的预处理方式。

这些指标应该一起阅读：PSNR/MAE 对像素变化敏感，SSIM 概括局部结构，颜色偏移揭示整体色调变化。模型和视频编码都会造成少量差异；若要设定合格线，建议先用同一工作流、相同分辨率和编码设置跑多组样本，再根据实际基线校准。
