from __future__ import annotations

import hashlib
import importlib
import logging
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageOps

from app.core.config import settings

logger = logging.getLogger(__name__)


class _ImageLRUCache:
    def __init__(self, max_items: int) -> None:
        self.max_items = max(1, max_items)
        self._items: OrderedDict[str, Image.Image] = OrderedDict()

    def get(self, key: str) -> Optional[Image.Image]:
        image = self._items.get(key)
        if image is None:
            return None
        self._items.move_to_end(key)
        return image.copy()

    def set(self, key: str, image: Image.Image) -> None:
        self._items[key] = image.copy()
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    @property
    def size(self) -> int:
        return len(self._items)


class CatVTONRuntime:
    def __init__(self) -> None:
        self._bootstrap_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._mask_cache = _ImageLRUCache(settings.CATVTON_MASK_CACHE_SIZE)
        self._warmup_thread: Optional[threading.Thread] = None

        self._loaded = False
        self._loading = False
        self._load_error: Optional[str] = None

        self._torch: Any = None
        self._pipeline: Any = None
        self._automasker: Any = None
        self._mask_processor: Any = None
        self._resize_and_crop: Any = None
        self._resize_and_padding: Any = None
        self._repaint_result: Any = None

        self._device: str = settings.CATVTON_DEVICE
        self._weights_path: Optional[str] = None
        self._base_model_path: Optional[str] = None

    def preload(self) -> None:
        if self._loaded:
            return

        self._loading = True
        with self._bootstrap_lock:
            if self._loaded:
                self._loading = False
                return

            try:
                self._load_impl()
                self._loaded = True
                self._load_error = None
                logger.info(
                    "CatVTON runtime is warm on %s (weights=%s).",
                    self._device,
                    self._weights_path,
                )
            except Exception as exc:  # pragma: no cover - hardware/dependency dependent
                self._load_error = str(exc)
                logger.exception("CatVTON preload failed")
                raise
            finally:
                self._loading = False

    def warmup(self) -> dict[str, Any]:
        if self._loaded:
            return self._warmup_payload("ready")

        if self._loading and self._warmup_thread and self._warmup_thread.is_alive():
            return self._warmup_payload("warming")

        def _runner() -> None:
            try:
                self.preload()
            except Exception:
                logger.exception("CatVTON async warmup failed")

        self._warmup_thread = threading.Thread(target=_runner, name="catvton-warmup", daemon=True)
        self._warmup_thread.start()
        return self._warmup_payload("warming")

    def predict(
        self,
        *,
        person_image_path: Path,
        garment_image_path: Path,
        category: str,
        output_path: Path,
        num_inference_steps: int,
        guidance_scale: float,
        seed: int,
    ) -> Path:
        self.preload()

        with self._inference_lock:
            person_image = ImageOps.exif_transpose(Image.open(person_image_path)).convert("RGB")
            garment_image = ImageOps.exif_transpose(Image.open(garment_image_path)).convert("RGB")
            original_size = person_image.size

            target_size = (settings.CATVTON_WIDTH, settings.CATVTON_HEIGHT)
            person_image = self._resize_and_crop(person_image, target_size)
            garment_image = self._resize_and_padding(garment_image, target_size)

            cloth_type = self._normalize_category(category)
            mask = self._build_mask(person_image, cloth_type)

            generator = None
            if seed >= 0:
                generator = self._torch.Generator(device=self._device).manual_seed(seed)

            with self._torch.inference_mode():
                result_image = self._pipeline(
                    image=person_image,
                    condition_image=garment_image,
                    mask=mask,
                    num_inference_steps=max(1, num_inference_steps),
                    guidance_scale=max(0.1, guidance_scale),
                    generator=generator,
                )[0]

            if settings.CATVTON_REPAINT:
                result_image = self._repaint_result(result_image, person_image, mask)

            if settings.CATVTON_PRESERVE_ORIGINAL_RESOLUTION and result_image.size != original_size:
                result_image = result_image.resize(original_size, Image.LANCZOS)

            output_path.parent.mkdir(parents=True, exist_ok=True)
            result_image.save(output_path, format="PNG")
            return output_path

    def status(self) -> dict[str, Any]:
        return {
            "loaded": self._loaded,
            "loading": self._loading,
            "device": self._device,
            "weights_path": self._weights_path,
            "base_model_path": self._base_model_path,
            "mask_cache_entries": self._mask_cache.size,
            "load_error": self._load_error,
        }

    def _load_impl(self) -> None:
        project_dir = settings.catvton_project_dir.resolve()
        if not project_dir.exists():
            raise RuntimeError(f"CatVTON project dir not found: {project_dir}")

        project_dir_str = str(project_dir)
        if project_dir_str not in sys.path:
            sys.path.insert(0, project_dir_str)

        torch = importlib.import_module("torch")
        image_processor_module = importlib.import_module("diffusers.image_processor")
        hub_module = importlib.import_module("huggingface_hub")
        cloth_masker_module = importlib.import_module("model.cloth_masker")
        pipeline_module = importlib.import_module("model.pipeline")
        utils_module = importlib.import_module("utils")

        self._device = settings.CATVTON_DEVICE
        if self._device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CatVTON embedded needs a CUDA GPU. Start this provider on RunPod / Linux NVIDIA.")

        if self._device == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = settings.CATVTON_ALLOW_TF32
            torch.backends.cudnn.allow_tf32 = settings.CATVTON_ALLOW_TF32
            torch.backends.cudnn.benchmark = True
            if hasattr(torch, "set_float32_matmul_precision"):
                torch.set_float32_matmul_precision("high")

        snapshot_download = getattr(hub_module, "snapshot_download")
        repo_path = self._resolve_model_source(settings.CATVTON_RESUME_PATH, snapshot_download)
        base_model_path = self._resolve_model_source(settings.CATVTON_BASE_MODEL_PATH, snapshot_download)
        weight_dtype = utils_module.init_weight_dtype(settings.CATVTON_MIXED_PRECISION)

        self._pipeline = pipeline_module.CatVTONPipeline(
            base_ckpt=base_model_path,
            attn_ckpt=repo_path,
            attn_ckpt_version="mix",
            weight_dtype=weight_dtype,
            use_tf32=settings.CATVTON_ALLOW_TF32,
            device=self._device,
            skip_safety_check=True,
        )
        self._mask_processor = image_processor_module.VaeImageProcessor(
            vae_scale_factor=8,
            do_normalize=False,
            do_binarize=True,
            do_convert_grayscale=True,
        )
        self._automasker = cloth_masker_module.AutoMasker(
            densepose_ckpt=str(Path(repo_path) / "DensePose"),
            schp_ckpt=str(Path(repo_path) / "SCHP"),
            device=self._device,
        )
        self._resize_and_crop = utils_module.resize_and_crop
        self._resize_and_padding = utils_module.resize_and_padding
        self._repaint_result = utils_module.repaint_result
        self._torch = torch
        self._weights_path = str(repo_path)
        self._base_model_path = str(base_model_path)

    def _build_mask(self, person_image: Image.Image, cloth_type: str) -> Image.Image:
        cache_key = self._mask_cache_key(person_image, cloth_type)
        cached_mask = self._mask_cache.get(cache_key)
        if cached_mask is None:
            generated = self._automasker(person_image, cloth_type)["mask"]
            self._mask_cache.set(cache_key, generated)
            cached_mask = generated
        return self._mask_processor.blur(cached_mask, blur_factor=settings.CATVTON_BLUR_FACTOR)

    @staticmethod
    def _mask_cache_key(person_image: Image.Image, cloth_type: str) -> str:
        payload = person_image.tobytes()
        digest = hashlib.sha256(payload).hexdigest()
        return f"{cloth_type}:{person_image.size[0]}x{person_image.size[1]}:{digest}"

    @staticmethod
    def _normalize_category(category: str) -> str:
        normalized = (category or "").strip().lower()
        if normalized in {"upper", "tops", "top", "upper_body", "shirt", "tshirt", "tee", "hoodie", "sweatshirt", "jacket"}:
            return "upper"
        if normalized in {"lower", "bottom", "bottoms", "lower_body", "pants", "jeans", "trousers", "joggers", "skirt"}:
            return "lower"
        if normalized in {"overall", "one-piece", "one-pieces", "dress", "jumpsuit", "full_body"}:
            return "overall"
        return "upper"

    @staticmethod
    def _resolve_model_source(reference: str, snapshot_download: Any) -> str:
        candidate = Path(reference).expanduser()
        if candidate.exists():
            return str(candidate.resolve())
        return str(snapshot_download(repo_id=reference))

    def _warmup_payload(self, state: str) -> dict[str, Any]:
        return {
            "status": state,
            "provider": "catvton",
            "ready": self._loaded,
            "loading": self._loading,
            "detail": self._load_error if state == "failed" else None,
        }


_runtime = CatVTONRuntime()


def preload_catvton_runtime() -> None:
    _runtime.preload()


def get_catvton_runtime() -> CatVTONRuntime:
    return _runtime


def get_catvton_runtime_status() -> dict[str, Any]:
    return _runtime.status()


def request_catvton_warmup() -> dict[str, Any]:
    return _runtime.warmup()
