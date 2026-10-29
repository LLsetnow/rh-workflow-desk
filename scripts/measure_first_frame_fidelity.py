#!/usr/bin/env python3
"""Compare an image-to-video reference image with the video's first decoded frame."""

from __future__ import annotations

import argparse
from array import array
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


DEFAULT_MAX_SIDE = 1024
SSIM_WINDOW_SIZE = 11
PIXEL_ERROR_THRESHOLD_PERCENT = 5.0


class MeasurementError(RuntimeError):
    """A readable error raised while preparing or comparing media."""


def _required_binary(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise MeasurementError(f"找不到 {name}。请先安装 FFmpeg（需包含 ffprobe）。")
    return executable


def _run(command: Sequence[str], label: str) -> bytes:
    try:
        result = subprocess.run(command, capture_output=True, check=False)
    except OSError as exc:
        raise MeasurementError(f"无法启动 {label}：{exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 1000:
            detail = detail[-1000:]
        raise MeasurementError(f"{label}失败。{detail or 'FFmpeg 未提供错误详情。'}")
    return result.stdout


def _resolve_input(value: str, label: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise MeasurementError(f"{label}不是可读文件：{path}")
    return path


def _extract_png(ffmpeg: str, source: Path, destination: Path, label: str) -> None:
    _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-c:v",
            "png",
            "-update",
            "1",
            str(destination),
        ],
        label,
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise MeasurementError(f"{label}没有生成有效的 PNG 帧。")


def _image_dimensions(ffprobe: str, image: Path) -> tuple[int, int]:
    output = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(image),
        ],
        "读取图像尺寸",
    )
    try:
        streams = json.loads(output).get("streams", [])
        width = int(streams[0]["width"])
        height = int(streams[0]["height"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MeasurementError(f"无法读取图像尺寸：{image.name}") from exc
    if width < 1 or height < 1:
        raise MeasurementError(f"图像尺寸无效：{image.name}")
    return width, height


def _comparison_size(frame_width: int, frame_height: int, max_side: int) -> tuple[int, int]:
    scale = min(1.0, max_side / max(frame_width, frame_height))
    return max(1, round(frame_width * scale)), max(1, round(frame_height * scale))


def _scale_filter(width: int, height: int, fit: str) -> str:
    size = f"{width}:{height}"
    if fit == "contain":
        return (
            f"scale={size}:force_original_aspect_ratio=decrease:"
            f"force_divisible_by=1:flags=lanczos,"
            f"pad={size}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    if fit == "cover":
        return (
            f"scale={size}:force_original_aspect_ratio=increase:"
            f"force_divisible_by=1:flags=lanczos,crop={size}"
        )
    return f"scale={size}:flags=lanczos"


def _normalized_rgb(
    ffmpeg: str,
    image: Path,
    width: int,
    height: int,
    fit: str,
) -> bytes:
    output = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            str(image),
            "-map",
            "0:v:0",
            "-vf",
            _scale_filter(width, height, fit),
            "-frames:v",
            "1",
            "-an",
            "-sn",
            "-dn",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        f"转换图像 {image.name}",
    )
    expected_bytes = width * height * 3
    if len(output) != expected_bytes:
        raise MeasurementError(
            f"图像转换后尺寸不符：预期 {expected_bytes} 字节，实际 {len(output)} 字节。"
        )
    return output


def _ssim_luma(reference: bytes, output: bytes, width: int, height: int) -> tuple[float, int]:
    """Return local SSIM on BT.709 luma using an 11x11 uniform window."""
    window = min(SSIM_WINDOW_SIZE, width, height)
    if window % 2 == 0:
        window -= 1
    if window < 3:
        raise MeasurementError("比较画面的短边不足 3 像素，无法计算 SSIM。")

    stride = width + 1
    integral_size = stride * (height + 1)
    sum_ref = array("d", [0.0]) * integral_size
    sum_out = array("d", [0.0]) * integral_size
    sum_ref_sq = array("d", [0.0]) * integral_size
    sum_out_sq = array("d", [0.0]) * integral_size
    sum_product = array("d", [0.0]) * integral_size

    for y in range(height):
        row_ref = 0.0
        row_out = 0.0
        row_ref_sq = 0.0
        row_out_sq = 0.0
        row_product = 0.0
        source_offset = y * width * 3
        integral_row = (y + 1) * stride
        previous_row = y * stride
        for x in range(width):
            pixel_offset = source_offset + x * 3
            ref_luma = (
                0.2126 * reference[pixel_offset]
                + 0.7152 * reference[pixel_offset + 1]
                + 0.0722 * reference[pixel_offset + 2]
            )
            out_luma = (
                0.2126 * output[pixel_offset]
                + 0.7152 * output[pixel_offset + 1]
                + 0.0722 * output[pixel_offset + 2]
            )
            row_ref += ref_luma
            row_out += out_luma
            row_ref_sq += ref_luma * ref_luma
            row_out_sq += out_luma * out_luma
            row_product += ref_luma * out_luma

            index = integral_row + x + 1
            above = previous_row + x + 1
            sum_ref[index] = sum_ref[above] + row_ref
            sum_out[index] = sum_out[above] + row_out
            sum_ref_sq[index] = sum_ref_sq[above] + row_ref_sq
            sum_out_sq[index] = sum_out_sq[above] + row_out_sq
            sum_product[index] = sum_product[above] + row_product

    samples = window * window
    sample_divisor = samples - 1
    c1 = (0.01 * 255.0) ** 2
    c2 = (0.03 * 255.0) ** 2
    score_sum = 0.0
    window_count = 0

    for y in range(height - window + 1):
        top = y * stride
        bottom = (y + window) * stride
        for x in range(width - window + 1):
            top_left = top + x
            top_right = top_left + window
            bottom_left = bottom + x
            bottom_right = bottom_left + window

            sx = (
                sum_ref[bottom_right]
                - sum_ref[top_right]
                - sum_ref[bottom_left]
                + sum_ref[top_left]
            )
            sy = (
                sum_out[bottom_right]
                - sum_out[top_right]
                - sum_out[bottom_left]
                + sum_out[top_left]
            )
            sxx = (
                sum_ref_sq[bottom_right]
                - sum_ref_sq[top_right]
                - sum_ref_sq[bottom_left]
                + sum_ref_sq[top_left]
            )
            syy = (
                sum_out_sq[bottom_right]
                - sum_out_sq[top_right]
                - sum_out_sq[bottom_left]
                + sum_out_sq[top_left]
            )
            sxy = (
                sum_product[bottom_right]
                - sum_product[top_right]
                - sum_product[bottom_left]
                + sum_product[top_left]
            )

            mean_ref = sx / samples
            mean_out = sy / samples
            variance_ref = max(0.0, (sxx - sx * sx / samples) / sample_divisor)
            variance_out = max(0.0, (syy - sy * sy / samples) / sample_divisor)
            covariance = (sxy - sx * sy / samples) / sample_divisor
            numerator = (2.0 * mean_ref * mean_out + c1) * (2.0 * covariance + c2)
            denominator = (mean_ref * mean_ref + mean_out * mean_out + c1) * (
                variance_ref + variance_out + c2
            )
            score_sum += numerator / denominator
            window_count += 1

    return score_sum / window_count, window


def _compute_metrics(
    reference: bytes,
    output: bytes,
    width: int,
    height: int,
) -> dict[str, object]:
    pixel_count = width * height
    absolute_by_channel = [0, 0, 0]
    signed_by_channel = [0, 0, 0]
    squared_error_sum = 0
    changed_pixel_count = 0
    threshold = 255.0 * PIXEL_ERROR_THRESHOLD_PERCENT / 100.0

    for offset in range(0, len(reference), 3):
        absolute_pixel_error = 0
        for channel in range(3):
            delta = output[offset + channel] - reference[offset + channel]
            absolute = abs(delta)
            absolute_by_channel[channel] += absolute
            signed_by_channel[channel] += delta
            squared_error_sum += delta * delta
            absolute_pixel_error += absolute
        if absolute_pixel_error / 3.0 >= threshold:
            changed_pixel_count += 1

    total_channel_count = pixel_count * 3
    mae = sum(absolute_by_channel) / total_channel_count
    mse = squared_error_sum / total_channel_count
    psnr = None if mse == 0 else 20.0 * math.log10(255.0 / math.sqrt(mse))
    ssim, window = _ssim_luma(reference, output, width, height)
    bounded_ssim = min(1.0, max(0.0, ssim))

    return {
        "ssim_luma": round(ssim, 8),
        "structure_difference_index_percent": round((1.0 - bounded_ssim) * 100.0, 4),
        "ssim_window_size": window,
        "rgb_mae_0_255": round(mae, 6),
        "rgb_mae_percent_of_range": round(mae / 255.0 * 100.0, 6),
        "psnr_db": None if psnr is None else round(psnr, 6),
        "perfect_pixel_match": mse == 0,
        "mean_absolute_error_by_channel": {
            "r": round(absolute_by_channel[0] / pixel_count, 6),
            "g": round(absolute_by_channel[1] / pixel_count, 6),
            "b": round(absolute_by_channel[2] / pixel_count, 6),
        },
        "mean_signed_color_shift_output_minus_reference": {
            "r": round(signed_by_channel[0] / pixel_count, 6),
            "g": round(signed_by_channel[1] / pixel_count, 6),
            "b": round(signed_by_channel[2] / pixel_count, 6),
        },
        "pixels_over_error_threshold_percent": PIXEL_ERROR_THRESHOLD_PERCENT,
        "pixels_over_error_threshold_ratio_percent": round(changed_pixel_count / pixel_count * 100.0, 6),
    }


def _preflight_outputs(args: argparse.Namespace, inputs: set[Path]) -> tuple[Path | None, Path | None]:
    destinations: list[Path] = []
    report_path = Path(args.json_out).expanduser().resolve() if args.json_out else None
    frame_path = Path(args.save_frame).expanduser().resolve() if args.save_frame else None
    for path in (report_path, frame_path):
        if path is None:
            continue
        if path in inputs:
            raise MeasurementError(f"输出路径不能覆盖输入文件：{path}")
        if path in destinations:
            raise MeasurementError("JSON 报告与导出首帧不能使用同一个路径。")
        if path.exists() and not args.force:
            raise MeasurementError(f"输出文件已存在；如需覆盖请添加 --force：{path}")
        destinations.append(path)
    if frame_path and frame_path.suffix.lower() != ".png":
        raise MeasurementError("--save-frame 的输出扩展名必须为 .png。")
    return report_path, frame_path


def _write_text(path: Path, content: str, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise MeasurementError(f"输出文件已存在；如需覆盖请添加 --force：{path}")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
        temporary_path.replace(path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def measure(args: argparse.Namespace) -> dict[str, object]:
    reference_path = _resolve_input(args.reference, "参考图")
    video_path = _resolve_input(args.video, "视频")
    report_path, frame_path = _preflight_outputs(args, {reference_path, video_path})
    ffmpeg = _required_binary("ffmpeg")
    ffprobe = _required_binary("ffprobe")

    with tempfile.TemporaryDirectory(prefix="rh-first-frame-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        reference_png = temporary_root / "reference.png"
        first_frame_png = temporary_root / "first-frame.png"
        _extract_png(ffmpeg, reference_path, reference_png, "解码参考图")
        _extract_png(ffmpeg, video_path, first_frame_png, "提取视频首帧")
        reference_width, reference_height = _image_dimensions(ffprobe, reference_png)
        frame_width, frame_height = _image_dimensions(ffprobe, first_frame_png)
        compare_width, compare_height = _comparison_size(frame_width, frame_height, args.max_side)

        reference_rgb = _normalized_rgb(
            ffmpeg, reference_png, compare_width, compare_height, args.fit
        )
        frame_rgb = _normalized_rgb(
            ffmpeg, first_frame_png, compare_width, compare_height, "stretch"
        )
        metrics = _compute_metrics(reference_rgb, frame_rgb, compare_width, compare_height)

        if frame_path:
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            if frame_path.exists() and not args.force:
                raise MeasurementError(f"输出文件已存在；如需覆盖请添加 --force：{frame_path}")
            shutil.copyfile(first_frame_png, frame_path)

    frame_aspect_ratio = frame_width / frame_height
    reference_aspect_ratio = reference_width / reference_height
    report: dict[str, object] = {
        "reference": {
            "path": str(reference_path),
            "width": reference_width,
            "height": reference_height,
        },
        "video": {
            "path": str(video_path),
            "first_frame_index": 0,
            "first_frame_width": frame_width,
            "first_frame_height": frame_height,
        },
        "comparison": {
            "fit": args.fit,
            "canvas_width": compare_width,
            "canvas_height": compare_height,
            "max_side": args.max_side,
            "resampling": "Lanczos",
            "reference_aspect_ratio": round(reference_aspect_ratio, 8),
            "video_aspect_ratio": round(frame_aspect_ratio, 8),
            "aspect_ratio_delta_percent": round(
                abs(reference_aspect_ratio - frame_aspect_ratio) / frame_aspect_ratio * 100.0,
                6,
            ),
            "ssim_color_component": "BT.709 luma",
            "ssim_window": "uniform local window",
            "rgb_metrics_color_space": "8-bit RGB",
        },
        "metrics": metrics,
        "interpretation": {
            "structure_difference_index_percent": (
                "100 × (1 − clamp(SSIM, 0, 1)); a structural difference index, "
                "not a subjective quality percentage."
            ),
            "psnr_db": "Higher is closer; null means a pixel-perfect match (infinite PSNR).",
            "pixels_over_error_threshold_ratio_percent": (
                "Share of canvas pixels whose mean absolute RGB error is at least 5% of the 8-bit range."
            ),
        },
    }
    if frame_path:
        report["saved_first_frame"] = str(frame_path)
    if report_path:
        _write_text(report_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n", args.force)
    return report


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("请输入正整数。") from exc
    if parsed < 16 or parsed > 2048:
        raise argparse.ArgumentTypeError("数值必须在 16 到 2048 之间。")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="比较参考图与图生视频首帧的结构、像素和色彩差异。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("reference", help="图生视频使用的参考图片")
    parser.add_argument("video", help="生成的视频文件")
    parser.add_argument(
        "--fit",
        choices=("contain", "cover", "stretch"),
        default="contain",
        help="参考图映射到视频画幅的方式：contain 保留全图并补黑边，cover 居中裁切，stretch 拉伸。",
    )
    parser.add_argument(
        "--max-side",
        type=_positive_int,
        default=DEFAULT_MAX_SIDE,
        help="比较画布长边上限；不会把低分辨率视频帧放大。",
    )
    parser.add_argument("--json-out", help="另存 JSON 报告；默认打印到终端。")
    parser.add_argument("--save-frame", help="另存解码得到的首帧 PNG。")
    parser.add_argument("--force", action="store_true", help="允许覆盖已存在的报告或首帧文件。")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = measure(args)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except (MeasurementError, OSError) as exc:
        print(f"首帧对比失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
