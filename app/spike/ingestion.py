"""Ingestion adapters and composite pipeline for Phase 0 Ingestion Feasibility Spike."""

from abc import ABC, abstractmethod
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import subprocess
import time
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel
from PIL import Image
import yt_dlp

from app.spike.config import settings
from app.spike.metadata import fetch_oembed_metadata
from app.spike.schemas import FailureCategory, FetchStatus
from app.spike.url_parser import SSRFValidationError

logger = logging.getLogger(__name__)

# Dedicated thread pool for blocking yt-dlp operations
_executor = ThreadPoolExecutor(max_workers=4)


class IngestionResult(BaseModel):
    """Result of an ingestion attempt by an adapter."""

    provider: str
    fetch_status: FetchStatus
    failure_category: FailureCategory = FailureCategory.NONE
    media_path: Optional[str] = None
    image_paths: List[str] = []
    caption: Optional[str] = None
    metadata: Dict[str, Any] = {}
    error_message: Optional[str] = None
    duration_seconds: float = 0.0
    caption_only: bool = False
    session_id: Optional[str] = None


class BaseIngestionAdapter(ABC):
    """Abstract base class for Reel ingestion adapters."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the adapter."""
        pass

    @abstractmethod
    async def fetch(
        self,
        url: str,
        shortcode: str,
        force_caption_only: bool = False,
        session_id: Optional[str] = None,
    ) -> IngestionResult:
        """Fetch media or metadata for the specified URL."""
        pass


class OEmbedAdapter(BaseIngestionAdapter):
    """Adapter that extracts caption and author metadata via oEmbed or OpenGraph."""

    @property
    def name(self) -> str:
        return "oembed"

    async def fetch(
        self,
        url: str,
        shortcode: str,
        force_caption_only: bool = False,
        session_id: Optional[str] = None,
    ) -> IngestionResult:
        start_time = time.perf_counter()
        try:
            meta = await fetch_oembed_metadata(url)
        except SSRFValidationError as ssrf_err:
            return IngestionResult(
                provider=self.name,
                fetch_status=FetchStatus.INVALID_URL,
                failure_category=FailureCategory.SSRF_ATTEMPT,
                error_message=f"SSRF validation error during metadata retrieval: {ssrf_err}",
                duration_seconds=round(time.perf_counter() - start_time, 3),
                caption_only=True,
                session_id=session_id,
            )

        duration = round(time.perf_counter() - start_time, 3)

        if not meta or not (meta.get("caption") or meta.get("title")):
            return IngestionResult(
                provider=self.name,
                fetch_status=FetchStatus.FAILED_TEMPORARY,
                failure_category=FailureCategory.POST_NOT_FOUND,
                error_message="oEmbed / OpenGraph returned no caption or metadata",
                duration_seconds=duration,
                caption_only=True,
                session_id=session_id,
            )

        caption_text = meta.get("caption") or meta.get("title") or ""
        return IngestionResult(
            provider=self.name,
            fetch_status=FetchStatus.SUCCESS_CAPTION_ONLY,
            failure_category=FailureCategory.NONE,
            caption=caption_text,
            metadata=meta,
            duration_seconds=duration,
            caption_only=True,
            session_id=session_id,
        )


class ServerYtDlpAdapter(BaseIngestionAdapter):
    """Direct server-side media downloader using yt-dlp with collision-safe filenames."""

    @property
    def name(self) -> str:
        return "server_ytdlp"

    def _classify_error(self, err_msg: str) -> tuple[FetchStatus, FailureCategory]:
        """Classify yt-dlp error string into standard statuses."""
        msg_lower = err_msg.lower()
        if any(term in msg_lower for term in ("login required", "sign in", "checkpoint", "redirected to login", "redirect to login", "abortextraction")):
            return FetchStatus.LOGIN_WALL, FailureCategory.LOGIN_WALL
        if any(term in msg_lower for term in ("private account", "account is private", "this account is private")):
            return FetchStatus.PRIVATE_ACCOUNT, FailureCategory.PRIVATE_ACCOUNT
        if any(term in msg_lower for term in ("does not exist", "404", "not found", "video has been removed", "post unavailable")):
            return FetchStatus.POST_NOT_FOUND, FailureCategory.POST_NOT_FOUND
        if any(term in msg_lower for term in ("rate limit", "too many requests", "429")):
            return FetchStatus.RATE_LIMITED, FailureCategory.RATE_LIMITED
        if "timed out" in msg_lower or "timeout" in msg_lower:
            return FetchStatus.FAILED_TEMPORARY, FailureCategory.TIMEOUT
        return FetchStatus.MEDIA_UNAVAILABLE, FailureCategory.MEDIA_DOWNLOAD_FAILED

    def _sync_download(
        self,
        url: str,
        shortcode: str,
        session_id: str,
        media_mode: str = "video_lowres",
    ) -> IngestionResult:
        """Synchronous yt-dlp extraction executed in thread pool."""
        start_time = time.perf_counter()
        file_prefix = f"{shortcode}_{session_id}"
        output_template = str(settings.temp_media_dir / f"{file_prefix}.%(ext)s")

        if media_mode == "audio":
            format_spec = "bestaudio[ext=m4a]/bestaudio/best"
        else:
            # Low-res video: 480p or lower to save bandwidth and compute
            format_spec = "best[height<=480][ext=mp4]/bestvideo[height<=480]+bestaudio/best[height<=480]/best[ext=mp4]/best"

        ydl_opts = {
            "outtmpl": output_template,
            "format": format_spec,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "max_filesize": settings.max_file_size_bytes,
            "noplaylist": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        }
        if settings.cookies_file and Path(settings.cookies_file).is_file():
            ydl_opts["cookiefile"] = str(settings.cookies_file)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                duration = round(time.perf_counter() - start_time, 3)

                if not info:
                    return IngestionResult(
                        provider=self.name,
                        fetch_status=FetchStatus.MEDIA_UNAVAILABLE,
                        failure_category=FailureCategory.MEDIA_DOWNLOAD_FAILED,
                        error_message="yt-dlp returned empty info dictionary",
                        duration_seconds=duration,
                        session_id=session_id,
                    )

                caption = info.get("description") or info.get("title") or ""
                metadata = {
                    "id": info.get("id"),
                    "title": info.get("title"),
                    "uploader": info.get("uploader"),
                    "uploader_id": info.get("uploader_id"),
                    "duration": info.get("duration"),
                    "view_count": info.get("view_count"),
                    "like_count": info.get("like_count"),
                    "ext": info.get("ext"),
                }

                # Locate downloaded file safely
                expected_file = None
                for ext in ("mp4", "mkv", "webm", "m4a", "mp3"):
                    candidate = settings.temp_media_dir / f"{file_prefix}.{ext}"
                    # Path traversal containment check
                    if not candidate.resolve().is_relative_to(settings.temp_media_dir.resolve()):
                        continue
                    if candidate.exists():
                        # Strict file size validation
                        file_size = candidate.stat().st_size
                        if file_size > settings.max_file_size_bytes:
                            candidate.unlink(missing_ok=True)
                            return IngestionResult(
                                provider=self.name,
                                fetch_status=FetchStatus.MEDIA_UNAVAILABLE,
                                failure_category=FailureCategory.MEDIA_DOWNLOAD_FAILED,
                                error_message=f"Downloaded file size ({file_size} bytes) exceeds limit ({settings.max_file_size_bytes} bytes)",
                                duration_seconds=duration,
                                session_id=session_id,
                            )
                        if file_size > 0:
                            expected_file = candidate
                            break

                if not expected_file or not expected_file.exists():
                    return IngestionResult(
                        provider=self.name,
                        fetch_status=FetchStatus.MEDIA_UNAVAILABLE,
                        failure_category=FailureCategory.MEDIA_DOWNLOAD_FAILED,
                        caption=caption,
                        metadata=metadata,
                        error_message="yt-dlp completed but media file was empty or missing",
                        duration_seconds=duration,
                        session_id=session_id,
                    )

                return IngestionResult(
                    provider=self.name,
                    fetch_status=FetchStatus.SUCCESS_FULL,
                    failure_category=FailureCategory.NONE,
                    media_path=str(expected_file),
                    caption=caption,
                    metadata=metadata,
                    duration_seconds=duration,
                    caption_only=False,
                    session_id=session_id,
                )

        except Exception as exc:
            duration = round(time.perf_counter() - start_time, 3)
            err_msg = str(exc)
            status, cat = self._classify_error(err_msg)
            if status == FetchStatus.LOGIN_WALL:
                err_msg = "unavailable: login required"
            return IngestionResult(
                provider=self.name,
                fetch_status=status,
                failure_category=cat,
                error_message=err_msg,
                duration_seconds=duration,
                session_id=session_id,
            )

    async def fetch(
        self,
        url: str,
        shortcode: str,
        force_caption_only: bool = False,
        session_id: Optional[str] = None,
        media_mode: str = "video_lowres",
    ) -> IngestionResult:
        sid = session_id or uuid.uuid4().hex[:8]
        if force_caption_only:
            return IngestionResult(
                provider=self.name,
                fetch_status=FetchStatus.SUCCESS_CAPTION_ONLY,
                failure_category=FailureCategory.NONE,
                error_message="Skipped yt-dlp because force_caption_only is enabled",
                caption_only=True,
                session_id=sid,
            )

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(_executor, self._sync_download, url, shortcode, sid, media_mode),
                timeout=settings.download_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return IngestionResult(
                provider=self.name,
                fetch_status=FetchStatus.FAILED_TEMPORARY,
                failure_category=FailureCategory.TIMEOUT,
                error_message=f"yt-dlp download timed out after {settings.download_timeout_seconds} seconds",
                duration_seconds=float(settings.download_timeout_seconds),
                session_id=sid,
            )


class GalleryDlAdapter(BaseIngestionAdapter):
    """Downloader for Instagram image and carousel posts using gallery-dl."""

    @property
    def name(self) -> str:
        return "gallery_dl"

    def _classify_error(self, err_msg: str) -> tuple[FetchStatus, FailureCategory]:
        """Classify gallery-dl output / error strings into standard statuses."""
        msg_lower = err_msg.lower()
        if any(term in msg_lower for term in ("redirect to login", "login page", "login required", "abortextraction", "sign in", "checkpoint")):
            return FetchStatus.LOGIN_WALL, FailureCategory.LOGIN_WALL
        if any(term in msg_lower for term in ("private account", "account is private", "this account is private")):
            return FetchStatus.PRIVATE_ACCOUNT, FailureCategory.PRIVATE_ACCOUNT
        if any(term in msg_lower for term in ("does not exist", "404", "not found", "post unavailable", "video has been removed")):
            return FetchStatus.POST_NOT_FOUND, FailureCategory.POST_NOT_FOUND
        if any(term in msg_lower for term in ("rate limit", "too many requests", "429")):
            return FetchStatus.RATE_LIMITED, FailureCategory.RATE_LIMITED
        if "timed out" in msg_lower or "timeout" in msg_lower:
            return FetchStatus.FAILED_TEMPORARY, FailureCategory.TIMEOUT
        return FetchStatus.MEDIA_UNAVAILABLE, FailureCategory.MEDIA_DOWNLOAD_FAILED

    def _sync_download(
        self,
        url: str,
        shortcode: str,
        session_id: str,
        max_images: int = 10,
    ) -> IngestionResult:
        """Runs gallery-dl to extract post images (up to max_images), caption, or video."""
        start_time = time.perf_counter()
        target_dir = settings.temp_media_dir / f"gdl_{shortcode}_{session_id}"
        target_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "gallery-dl",
            "--range", f"1-{max_images}",
            "-D", str(target_dir),
            "--write-metadata",
            "--no-mtime",
            "--quiet",
        ]

        if settings.cookies_file and Path(settings.cookies_file).is_file():
            cmd.extend(["--cookies", str(settings.cookies_file)])

        cmd.append(url)

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=settings.download_timeout_seconds,
            )
            duration = round(time.perf_counter() - start_time, 3)
            all_err_text = ((res.stderr or "") + " " + (res.stdout or "")).strip()

            downloaded_files = [f for f in target_dir.rglob("*") if f.is_file() and not f.name.endswith(".part")]

            video_files = [
                f for f in downloaded_files
                if f.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv")
            ]
            image_files = [
                f for f in downloaded_files
                if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
            ]
            json_files = [
                f for f in downloaded_files
                if f.suffix.lower() == ".json"
            ]

            caption = ""
            metadata = {}
            for jf in json_files:
                try:
                    with open(jf, "r", encoding="utf-8") as jf_in:
                        meta_data = json.load(jf_in)
                        if isinstance(meta_data, dict):
                            caption = caption or meta_data.get("description") or meta_data.get("caption") or meta_data.get("title") or ""
                            metadata.update({k: v for k, v in meta_data.items() if k not in ("cookies", "password", "session")})
                except Exception:
                    pass

            # 1. If any video files exist, use video path
            if video_files:
                chosen_video = sorted(video_files)[0]
                return IngestionResult(
                    provider=self.name,
                    fetch_status=FetchStatus.SUCCESS_FULL,
                    failure_category=FailureCategory.NONE,
                    media_path=str(chosen_video),
                    image_paths=[],
                    caption=caption,
                    metadata=metadata,
                    duration_seconds=duration,
                    caption_only=False,
                    session_id=session_id,
                )

            # 2. If image files exist, resize to long side ~1024px and return image_paths
            if image_files:
                sorted_images = sorted(image_files)[:max_images]
                resized_image_paths = []
                for img_path in sorted_images:
                    try:
                        with Image.open(img_path) as im:
                            im_rgb = im.convert("RGB")
                            w, h = im_rgb.size
                            if max(w, h) > 1024:
                                scale = 1024.0 / max(w, h)
                                new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                                im_rgb = im_rgb.resize(new_size, Image.Resampling.LANCZOS)
                            resized_path = img_path.parent / f"resized_{img_path.name}"
                            im_rgb.save(resized_path, format="JPEG", quality=85)
                            resized_image_paths.append(str(resized_path))
                    except Exception as resize_err:
                        logger.warning("Error resizing %s: %s; using original", img_path, resize_err)
                        resized_image_paths.append(str(img_path))

                return IngestionResult(
                    provider=self.name,
                    fetch_status=FetchStatus.SUCCESS_FULL,
                    failure_category=FailureCategory.NONE,
                    media_path=None,
                    image_paths=resized_image_paths,
                    caption=caption,
                    metadata=metadata,
                    duration_seconds=duration,
                    caption_only=False,
                    session_id=session_id,
                )

            # 3. No media files downloaded: classify error
            status, cat = self._classify_error(all_err_text)
            err_msg = all_err_text or "No images or video could be extracted"
            if status == FetchStatus.LOGIN_WALL:
                err_msg = "unavailable: login required"

            return IngestionResult(
                provider=self.name,
                fetch_status=status,
                failure_category=cat,
                error_message=err_msg,
                duration_seconds=duration,
                session_id=session_id,
            )

        except subprocess.TimeoutExpired:
            return IngestionResult(
                provider=self.name,
                fetch_status=FetchStatus.FAILED_TEMPORARY,
                failure_category=FailureCategory.TIMEOUT,
                error_message=f"gallery-dl download timed out after {settings.download_timeout_seconds} seconds",
                duration_seconds=float(settings.download_timeout_seconds),
                session_id=session_id,
            )
        except Exception as exc:
            duration = round(time.perf_counter() - start_time, 3)
            status, cat = self._classify_error(str(exc))
            err_msg = str(exc)
            if status == FetchStatus.LOGIN_WALL:
                err_msg = "unavailable: login required"
            return IngestionResult(
                provider=self.name,
                fetch_status=status,
                failure_category=cat,
                error_message=err_msg,
                duration_seconds=duration,
                session_id=session_id,
            )

    async def fetch(
        self,
        url: str,
        shortcode: str,
        force_caption_only: bool = False,
        session_id: Optional[str] = None,
        max_images: int = 10,
    ) -> IngestionResult:
        sid = session_id or uuid.uuid4().hex[:8]
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_executor, self._sync_download, url, shortcode, sid, max_images)


class CompositeIngestionPipeline:
    """
    Coordinates ingestion attempts with fallback logging.
    """

    def __init__(self):
        self.oembed_adapter = OEmbedAdapter()
        self.ytdlp_adapter = ServerYtDlpAdapter()
        self.gallery_dl_adapter = GalleryDlAdapter()

    async def ingest(
        self,
        url: str,
        shortcode: str,
        force_caption_only: bool = False,
        session_id: Optional[str] = None,
        media_mode: str = "video_lowres",
    ) -> tuple[IngestionResult, bool]:
        sid = session_id or uuid.uuid4().hex[:8]

        # Case 1: Caller requested caption-only or server downloading disabled
        if force_caption_only or not settings.enable_server_ytdlp:
            res = await self.oembed_adapter.fetch(url, shortcode, force_caption_only=True, session_id=sid)
            return res, False

        # Case 2: Post / Carousel route (/p/ or /tv/)
        is_post_route = "/p/" in url.lower() or "/tv/" in url.lower()
        if is_post_route:
            # 1. Try gallery-dl for images (or carousel videos)
            gdl_res = await self.gallery_dl_adapter.fetch(url, shortcode, session_id=sid, max_images=10)
            if gdl_res.fetch_status == FetchStatus.SUCCESS_FULL and (gdl_res.image_paths or gdl_res.media_path):
                return gdl_res, False
            if gdl_res.fetch_status == FetchStatus.LOGIN_WALL:
                return gdl_res, False

            # 2. If post contains video not caught by gallery-dl, try yt-dlp
            ytdlp_res = await self.ytdlp_adapter.fetch(url, shortcode, session_id=sid, media_mode=media_mode)
            if ytdlp_res.fetch_status == FetchStatus.SUCCESS_FULL and ytdlp_res.media_path:
                return ytdlp_res, False
            if ytdlp_res.fetch_status == FetchStatus.LOGIN_WALL:
                return ytdlp_res, False

            # 3. Fallback to oEmbed caption if available
            oembed_res = await self.oembed_adapter.fetch(url, shortcode, session_id=sid)
            if oembed_res.fetch_status == FetchStatus.SUCCESS_CAPTION_ONLY and oembed_res.caption:
                return oembed_res, True

            return gdl_res, False

        # Case 3: Reel route (/reel/ or /reels/)
        ytdlp_res = await self.ytdlp_adapter.fetch(url, shortcode, session_id=sid, media_mode=media_mode)
        if ytdlp_res.fetch_status == FetchStatus.SUCCESS_FULL and ytdlp_res.media_path:
            return ytdlp_res, False

        # Case 4: Reel download failed; attempt caption-only fallback via oEmbed
        logger.info(
            "yt-dlp download unsuccessful for %s (%s). Attempting caption fallback.",
            shortcode,
            ytdlp_res.fetch_status,
        )
        oembed_res = await self.oembed_adapter.fetch(url, shortcode, session_id=sid)
        if oembed_res.fetch_status == FetchStatus.SUCCESS_CAPTION_ONLY and oembed_res.caption:
            oembed_res.metadata["ytdlp_failure_category"] = ytdlp_res.failure_category.value
            oembed_res.metadata["ytdlp_error"] = ytdlp_res.error_message
            return oembed_res, True

        return ytdlp_res, False
