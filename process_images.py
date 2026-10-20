"""원본 상품 이미지(370px) → 업스케일 → 배경 제거 → 크기 통일 → 투명 WebP.

CU 원본은 두 종류다(확장자는 모두 .jpg).
  - 흰 배경 JPEG (343장)
  - 투명 배경 PNG (107장): 디자이너가 따 둔 누끼. 투명 영역 밑 RGB에는 가장자리 색이
    늘어나 채워져 있어서, 투명도를 버리고 RGB만 쓰면 줄무늬가 드러난다.

두 단계로 나눠 실행한다. PyTorch(CUDA)와 DirectML을 한 프로세스에서 같이 쓰면
GPU 메모리 충돌로 배경 제거 모델이 실패하기 때문이다.

  1) upscale : Real-ESRGAN으로 4배 업스케일 → PNG 저장 (NVIDIA GPU는 CUDA)
               투명 PNG는 투명도도 같은 모델로 업스케일해 upscaled_alpha/에 저장
  2) cutout  : 투명 PNG → 업스케일한 원본 투명도를 그대로 사용
               흰 배경  → 테두리와 이어진 흰 영역만 배경으로 분리하고, 색만으로 애매한
                          흰 가장자리는 BiRefNet/ISNet ONNX 마스크로 보완한 뒤
                          배경이 순백(255)이라는 점을 이용해 경계의 흰 번짐을 역산해 제거
               → 상품 영역만 잘라 CANVAS 정사각 캔버스 가운데에 긴 변 FIT 픽셀로 맞춤
                 (모든 상품이 화면에서 같은 크기로 보인다)

사용법:
  .venv/Scripts/python process_images.py upscale data/raw_images data/upscaled [--sr x4plus|general]
  .venv/Scripts/python process_images.py cutout  data/upscaled data/cutout [--bg birefnet-general-lite|isnet-general-use|none] [--bg-cpu]
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

MODELS = Path(__file__).parent / "models"
SR_FILES = {"x4plus": "RealESRGAN_x4plus.pth", "general": "realesr-general-x4v3.pth"}
BG_NORM = {  # rembg와 같은 전처리 값
    "birefnet-general": ((0.485, 0.456, 0.406), (0.229, 0.224, 0.225), True),
    "birefnet-general-lite": ((0.485, 0.456, 0.406), (0.229, 0.224, 0.225), True),
    "isnet-general-use": ((0.5, 0.5, 0.5), (1.0, 1.0, 1.0), False),
}
CANVAS = 800   # 출력 이미지 한 변(px)
FIT = 720      # 상품의 긴 변이 차지할 크기(px)
WHITE_LO = 228  # 원본에서 가장 어두운 채널이 이 값 이상이면 '배경일 수 있는 흰색'
WHITE_HI = 250  # 이 값 이상이면 완전한 배경(투명)
PURE_WHITE = 253  # 이 값 이상인 순백 영역은 색 있는 부분의 외곽선 안일 때만 AI 마스크를 더한다


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# ---------- 1단계: 업스케일 ----------

def has_alpha(im):
    """CU 이미지 중 일부는 확장자만 .jpg이고 실제로는 투명 배경 PNG다."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        return bool((np.asarray(im.convert("RGBA").getchannel("A")) < 250).any())
    return False


def run_upscale(src, dst, sr, force):
    """RGB를 업스케일한다. 원본에 투명도가 있으면 투명도도 같은 모델로 업스케일해
    dst 옆의 upscaled_alpha/ 폴더에 저장한다.

    투명 PNG의 RGB는 투명 영역 밑에 가장자리 색을 늘려 채워 둔(edge bleed) 상태라,
    그대로 업스케일하면 경계에 흰 테두리나 검은 테두리가 생기지 않는다.
    """
    import torch
    from spandrel import ModelLoader

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = ModelLoader().load_from_file(str(MODELS / SR_FILES[sr])).eval().to(device)
    half = device == "cuda"
    if half:
        model.model.half()
    log(f"업스케일: {sr} ({device})")
    alpha_dir = Path(dst).parent / "upscaled_alpha"
    alpha_dir.mkdir(parents=True, exist_ok=True)

    def sr3(arr):
        x = torch.from_numpy(arr).permute(2, 0, 1)[None].to(device)
        y = model(x.half() if half else x).float().clamp(0, 1)[0]
        return y.permute(1, 2, 0).cpu().numpy()

    files = sorted(Path(src).glob("*.jpg"))
    t0 = time.time()
    with torch.inference_mode():
        for i, f in enumerate(files, 1):
            im = Image.open(f)
            out = dst / (f.stem + ".png")
            if force or not out.exists():
                y = sr3(np.asarray(im.convert("RGB"), dtype=np.float32) / 255.0)
                Image.fromarray((y * 255).round().astype(np.uint8)).save(out, compress_level=1)
            aout = alpha_dir / (f.stem + ".png")
            if has_alpha(im) and (force or not aout.exists()):
                a = np.asarray(im.convert("RGBA").getchannel("A"), dtype=np.float32) / 255.0
                y = sr3(np.repeat(a[..., None], 3, axis=2)).mean(axis=2)
                Image.fromarray((y * 255).round().astype(np.uint8), "L").save(aout, compress_level=1)
            if i % 25 == 0 or i == len(files):
                log(f"  {i}/{len(files)}  {time.time() - t0:.0f}s")


# ---------- 2단계: 배경 제거 + 크기 통일 ----------

class Matter:
    def __init__(self, name, cpu=False):
        import onnxruntime as ort

        self.name = name
        use_dml = not cpu and "DmlExecutionProvider" in ort.get_available_providers()
        self.sess = self._session(use_dml)
        self.cpu_sess = None if use_dml else self.sess
        self.input = self.sess.get_inputs()[0].name
        self.mean, self.std, self.sigmoid = BG_NORM[name]

    def _session(self, dml):
        import onnxruntime as ort

        # DirectML은 메모리 패턴 최적화와 병렬 실행을 지원하지 않는다. 켜 두면 메모리 부족으로 실패한다.
        so = ort.SessionOptions()
        so.enable_mem_pattern = False
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.log_severity_level = 3
        providers = ["CPUExecutionProvider"]
        if dml:
            providers.insert(0, ("DmlExecutionProvider", {"performance_preference": "high_performance"}))
        return ort.InferenceSession(str(MODELS / f"{self.name}.onnx"), sess_options=so, providers=providers)

    def _run(self, x):
        try:
            return self.sess.run(None, {self.input: x})[0]
        except Exception:
            # DirectML이 간헐적으로 GPU 메모리 부족을 내면 이 장만 CPU로 다시 계산
            if self.cpu_sess is None:
                self.cpu_sess = self._session(False)
            if self.sess is self.cpu_sess:
                raise
            log("  (GPU 메모리 부족 → 이 장은 CPU로 처리)")
            return self.cpu_sess.run(None, {self.input: x})[0]

    def __call__(self, img):
        im = img.resize((1024, 1024), Image.LANCZOS)
        a = (np.asarray(im, dtype=np.float32) / 255.0 - self.mean) / self.std
        pred = self._run(a.transpose(2, 0, 1)[None].astype(np.float32))[0, 0]
        if self.sigmoid:
            pred = 1 / (1 + np.exp(-pred))
        pred = (pred - pred.min()) / max(pred.max() - pred.min(), 1e-6)
        m = Image.fromarray((pred * 255).astype(np.uint8)).resize(img.size, Image.LANCZOS)
        return np.asarray(m, dtype=np.float32) / 255.0


def color_alpha(orig):
    """원본(370px)에서 '테두리와 이어진 흰 영역'만 배경으로 본 알파.

    - 테두리에서 흰색으로만 이어진 영역 = 배경. 그 안에서도 덜 흰 픽셀은 경계로 보고 부드럽게.
    - 상품 윤곽 안쪽의 흰 부분(빵, 밥, 흰 포장)과 흰색이 아닌 부분(용기, 소스컵)은 항상 1.
    """
    from scipy import ndimage

    minc = np.asarray(orig, dtype=np.float32).min(axis=2) / 255.0
    lo, hi = WHITE_LO / 255.0, WHITE_HI / 255.0
    labels, _ = ndimage.label(minc >= lo)
    edge = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    bg = np.isin(labels, np.unique(edge[edge > 0]))
    return np.where(bg, np.clip((hi - minc) / (hi - lo), 0, 1), 1.0).astype(np.float32)


def convex_hull_mask(mask):
    """mask의 볼록 외곽선 내부. 행마다 양 끝 점만 모아 monotone chain으로 외곽선을 구한다."""
    from PIL import ImageDraw

    ys = np.nonzero(mask.any(axis=1))[0]
    if len(ys) == 0:
        return mask
    pts = set()
    for y in ys:
        xs = np.nonzero(mask[y])[0]
        pts.update({(int(xs[0]), int(y)), (int(xs[-1]), int(y))})
    pts = sorted(pts)

    def half(points):
        out = []
        for p in points:
            while len(out) >= 2 and (out[-1][0] - out[-2][0]) * (p[1] - out[-2][1]) - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0]) <= 0:
                out.pop()
            out.append(p)
        return out

    hull = half(pts)[:-1] + half(pts[::-1])[:-1]
    img = Image.new("L", (mask.shape[1], mask.shape[0]), 0)
    if len(hull) >= 3:
        ImageDraw.Draw(img).polygon(hull, fill=255, outline=255)
    return np.asarray(img) > 0


def ai_allowed(orig, color_fg, size):
    """AI 마스크를 더해도 되는 곳 (원본 370px 기준으로 계산 후 확대).

    - 원본에서 완전한 순백이 아닌 곳(+3px): 흰 포장의 미세한 음영
    - 색 있는 부분의 볼록 외곽선 안: 로고와 테두리 사이의 순백 컵 몸통 같은 곳
    AI가 상상으로 덧붙이는 영역(컵 아래 바닥 등)은 둘 다 아니다.
    """
    from scipy import ndimage

    minc = np.asarray(orig).min(axis=2)
    shaded = ndimage.binary_dilation(minc < PURE_WHITE, iterations=3)
    hull = ndimage.binary_dilation(convex_hull_mask(color_fg), iterations=2)
    return np.asarray(Image.fromarray(shaded | hull).resize(size, Image.NEAREST))


def touching(mask, anchor):
    """mask의 연결 덩어리 중 anchor와 겹치는 것만 남긴다 (상품과 떨어진 오검출 제거)."""
    from scipy import ndimage

    labels, _ = ndimage.label(mask)
    keep = np.unique(labels[anchor & mask])
    return np.isin(labels, keep[keep > 0])


def unmix_white(rgb, alpha):
    """관측색 I = a*F + (1-a)*흰색 → F = (I - (1-a)) / a. 경계의 흰 번짐 제거."""
    a = alpha[..., None]
    fg = (rgb - (1 - a)) / np.maximum(a, 1e-3)
    return np.where(a > 0.02, np.clip(fg, 0, 1), rgb)


def normalize(rgba):
    alpha = np.asarray(rgba.getchannel("A"))
    ys, xs = np.nonzero(alpha > 8)
    if len(xs) == 0:
        return None
    crop = rgba.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)).convert("RGBa")
    scale = FIT / max(crop.size)
    size = (max(1, round(crop.width * scale)), max(1, round(crop.height * scale)))
    crop = crop.resize(size, Image.LANCZOS).convert("RGBA")
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((CANVAS - crop.width) // 2, (CANVAS - crop.height) // 2))
    return canvas


def cutout(img, orig, matter, mask_path=None):
    """흰 배경 JPEG용: 테두리와 이어진 흰 영역을 배경으로 분리하고, AI 마스크로 흰 가장자리를 보완."""
    rgb = np.asarray(img, dtype=np.float32) / 255.0
    ca_small = color_alpha(orig)
    ca = Image.fromarray(ca_small, "F").resize(img.size, Image.BILINEAR)
    alpha = np.asarray(ca, dtype=np.float32)
    if matter is not None:
        # AI 마스크는 상품 가장자리의 흰 포장처럼 색만으로 애매한 곳을 메우는 데만 쓴다.
        # 계산이 비싸서(장당 수 초) 한 번 구한 마스크는 저장해 두고 다시 쓴다.
        if mask_path and mask_path.exists():
            ai = np.asarray(Image.open(mask_path), dtype=np.float32) / 255.0
        else:
            ai = matter(img)
            if mask_path:
                Image.fromarray((ai * 255).round().astype(np.uint8)).save(mask_path)
        ok = touching(ai > 0.5, alpha > 0.5) & ai_allowed(orig, ca_small > 0.5, img.size)
        alpha = np.maximum(alpha, np.where(ok, ai, 0))
    alpha = np.where(alpha < 0.02, 0, alpha)
    rgba = np.dstack([unmix_white(rgb, alpha), alpha])
    return normalize(Image.fromarray((rgba * 255).round().astype(np.uint8), "RGBA"))


def drop_specks(alpha, min_ratio=0.002):
    """가장 큰 덩어리의 0.2% 미만인 떨어진 점(업스케일 잡음)을 지운다. 따로 떨어진 스티커·소스컵은 남는다."""
    from scipy import ndimage

    m = alpha > 0.5
    labels, n = ndimage.label(m)
    if n <= 1:
        return alpha
    sizes = ndimage.sum(m, labels, range(1, n + 1))
    keep = np.isin(labels, 1 + np.nonzero(sizes >= sizes.max() * min_ratio)[0])
    return np.where(ndimage.binary_dilation(keep, iterations=4), alpha, 0)


def cutout_alpha(img, alpha_img):
    """투명 PNG 원본용: 원본의 투명도(디자이너가 따 둔 누끼)를 업스케일해 그대로 쓴다."""
    rgb = np.asarray(img, dtype=np.float32) / 255.0
    alpha = np.asarray(alpha_img, dtype=np.float32) / 255.0
    alpha = np.where(alpha < 0.02, 0, np.where(alpha > 0.98, 1, alpha))
    alpha = drop_specks(alpha)
    rgba = np.dstack([rgb, alpha])
    return normalize(Image.fromarray((rgba * 255).round().astype(np.uint8), "RGBA"))


def run_cutout(src, dst, orig_dir, bg, cpu, force, shard, only=None):
    """원본에 투명도가 있으면(upscaled_alpha/에 파일이 있으면) 그 투명도를, 없으면 흰 배경 분리를 쓴다."""
    k, n = map(int, shard.split("/"))
    files = sorted(Path(src).glob("*.png"))
    if only:
        keep = set(Path(only).read_text(encoding="utf-8").split())
        files = [f for f in files if f.stem in keep]
    files = files[k::n]
    alpha_dir = Path(src).parent / "upscaled_alpha"
    needs_ai = any(not (alpha_dir / f.name).exists() for f in files)
    matter = Matter(bg, cpu=cpu) if needs_ai and bg != "none" else None
    cache = Path(src).parent / "ai_masks" / bg
    if matter is not None:
        cache.mkdir(parents=True, exist_ok=True)
    log(f"배경 제거: 투명 PNG는 원본 투명도, 흰 배경은 색 분리 + {bg if matter else '-'}")
    t0 = time.time()
    for i, f in enumerate(files, 1):
        out = dst / (f.stem + ".webp")
        if out.exists() and not force:
            continue
        img = Image.open(f).convert("RGB")
        a = alpha_dir / f.name
        if a.exists():
            final = cutout_alpha(img, Image.open(a).convert("L"))
        else:
            orig = Image.open(Path(orig_dir) / (f.stem + ".jpg")).convert("RGB")
            final = cutout(img, orig, matter, cache / f.name if matter else None)
        if final is None:
            log(f"  [건너뜀] 상품 영역 없음: {f.name}")
            continue
        final.save(out, "WEBP", quality=84, method=4)
        if i % 25 == 0 or i == len(files):
            log(f"  {i}/{len(files)}  {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["upscale", "cutout"])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--sr", default="x4plus", choices=SR_FILES)
    ap.add_argument("--bg", default="birefnet-general-lite", choices=[*BG_NORM, "none"])
    ap.add_argument("--bg-cpu", action="store_true", help="배경 제거 모델을 CPU로 실행 (GPU 메모리가 부족할 때)")
    ap.add_argument("--orig", default="data/raw_images", help="cutout 단계에서 참조할 원본 이미지 폴더")
    ap.add_argument("--shard", default="0/1", help="k/n: 파일 목록을 n개로 나눠 k번째만 처리 (프로세스 병렬 실행용)")
    ap.add_argument("--only", help="처리할 바코드 목록 파일(공백·줄바꿈 구분)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    if args.step == "upscale":
        run_upscale(args.src, dst, args.sr, args.force)
    else:
        run_cutout(args.src, dst, args.orig, args.bg, args.bg_cpu, args.force, args.shard, args.only)


if __name__ == "__main__":
    main()
