"""FastAPI Router for Reel Vault personal knowledge library and UI."""

from datetime import datetime, timezone
import logging
from typing import List, Optional
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from app.spike.config import settings
from app.spike.url_parser import InvalidInstagramURLError, SSRFValidationError, parse_and_normalize_url
from app.vault.auth import clear_auth_cookie, get_expected_token, is_authenticated, set_auth_cookie
from app.vault.backup import export_library_backup, restore_library_backup
from app.vault.database import (
    delete_reel,
    get_daily_processed_count,
    get_reel_by_shortcode,
    get_vault_stats,
    queue_reel,
    retry_reel,
    search_library,
    toggle_bookmark,
)
from app.vault.importer import import_legacy_reels
from app.vault.models import (
    AddReelRequest,
    AddReelResponse,
    BackupRestoreResponse,
    BookmarkResponse,
    DeleteReelResponse,
    RetryReelResponse,
    VaultListResponse,
    VaultReel,
    VaultStats,
)
from app.vault.pwa import (
    extract_instagram_url_from_share,
    get_service_worker_script,
    get_web_app_manifest,
)
from app.vault.ui import render_login_page, render_main_vault_page, render_reel_detail_page

logger = logging.getLogger(__name__)

vault_router = APIRouter(tags=["Vault Library"])


# ==============================================================================
# Web UI Pages
# ==============================================================================


@vault_router.get("/", response_class=HTMLResponse)
async def vault_home(request: Request):
    """Renders the main personal knowledge vault dashboard (or login page if unauthenticated)."""
    if not is_authenticated(request):
        return HTMLResponse(content=render_login_page(), status_code=status.HTTP_200_OK)
    return HTMLResponse(content=render_main_vault_page(), status_code=status.HTTP_200_OK)


@vault_router.get("/reels/{shortcode}", response_class=HTMLResponse)
async def vault_detail_page(shortcode: str, request: Request):
    """Renders a standalone, bookmarkable detail page for a specific Reel."""
    if not is_authenticated(request):
        return HTMLResponse(content=render_login_page(), status_code=status.HTTP_200_OK)

    reel = get_reel_by_shortcode(shortcode)
    if not reel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Reel '{shortcode}' not found in vault.")
    return HTMLResponse(content=render_reel_detail_page(reel), status_code=status.HTTP_200_OK)


@vault_router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, redirect: Optional[str] = None):
    """Renders login page."""
    if is_authenticated(request):
        target = redirect if (redirect and redirect.startswith("/")) else "/"
        return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
    return HTMLResponse(content=render_login_page(redirect_url=redirect), status_code=status.HTTP_200_OK)


@vault_router.post("/login")
async def login_post(
    request: Request,
    response: Response,
    token: str = Form(...),
    redirect: Optional[str] = Form(None),
):
    """Validates the shared password/token and sets authentication cookie."""
    expected = get_expected_token()
    if not expected or token.strip() == expected:
        target_url = redirect if (redirect and redirect.startswith("/")) else "/"
        redirect_resp = RedirectResponse(url=target_url, status_code=status.HTTP_302_FOUND)
        set_auth_cookie(redirect_resp, token.strip())
        return redirect_resp

    return HTMLResponse(
        content=render_login_page(
            error_message="Invalid access token/password. Please try again.",
            redirect_url=redirect,
        ),
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


@vault_router.get("/logout")
async def logout():
    """Logs out and clears the authentication cookie."""
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
    clear_auth_cookie(redirect)
    return redirect


# ==============================================================================
# PWA & Web Share Target Endpoints
# ==============================================================================


@vault_router.get("/manifest.json")
@vault_router.get("/manifest.webmanifest")
async def pwa_manifest():
    """Serves the W3C Web App Manifest."""
    return JSONResponse(
        content=get_web_app_manifest(),
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@vault_router.get("/sw.js")
async def pwa_service_worker():
    """Serves the PWA service worker script."""
    return Response(
        content=get_service_worker_script(),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@vault_router.post("/share-target")
async def share_target_post(
    request: Request,
    title: Optional[str] = Form(None),
    text: Optional[str] = Form(None),
    url: Optional[str] = Form(None),
):
    """
    Handles Android Web Share Target POST submissions from Instagram.
    Accepts title, text, or url payload fields and queues the extracted Reel.
    """
    return await _process_share_target(request, title=title, text=text, url=url)


@vault_router.get("/share-target")
async def share_target_get(
    request: Request,
    title: Optional[str] = None,
    text: Optional[str] = None,
    url: Optional[str] = None,
):
    """Fallback handler for GET share target requests."""
    return await _process_share_target(request, title=title, text=text, url=url)


async def _process_share_target(
    request: Request,
    title: Optional[str],
    text: Optional[str],
    url: Optional[str],
):
    # 1. Extract valid Instagram URL
    extracted_url = extract_instagram_url_from_share(title=title, text=text, url=url)

    if not extracted_url:
        return RedirectResponse(
            url="/?share_error=No+valid+Instagram+Reel+link+found+in+shared+data.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # 2. Check authentication
    if not is_authenticated(request):
        encoded_dest = quote(f"/share-target?url={extracted_url}", safe="")
        return RedirectResponse(
            url=f"/login?redirect={encoded_dest}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # 3. Parse and normalize URL (preserves original /reel/ vs /p/)
    try:
        norm_url, shortcode, path_type = parse_and_normalize_url(extracted_url)
    except (InvalidInstagramURLError, SSRFValidationError) as err:
        return RedirectResponse(
            url=f"/?share_error={quote(str(err))}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # 4. Submit to library queue
    reel, is_new, msg = queue_reel(
        original_url=extracted_url,
        normalized_url=norm_url,
        shortcode=shortcode,
        path_type=path_type,
    )

    # 5. Redirect to vault with feedback parameters
    is_post = reel.path_type in ("post", "p", "tv")
    redirect_data = {
        "shared": shortcode,
        "is_new": 1 if is_new else 0,
        "status": reel.status,
        "msg": msg,
    }
    if is_post:
        redirect_data["open_quick_note"] = 1

    params = urlencode(redirect_data)
    return RedirectResponse(
        url=f"/?{params}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# ==============================================================================
# Vault Library API Endpoints
# ==============================================================================


@vault_router.post("/api/vault/reels", response_model=AddReelResponse)
async def add_reel_to_vault(payload: AddReelRequest, request: Request):
    """
    Submits a Reel URL to be added to the vault.
    Validates the URL, creates or reuses the record, and queues background processing.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    # Validate Instagram URL & shortcode
    try:
        norm_url, shortcode, path_type = parse_and_normalize_url(payload.url)
    except (InvalidInstagramURLError, SSRFValidationError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Queue or reuse existing reel
    reel, is_new, msg = queue_reel(
        original_url=payload.url,
        normalized_url=norm_url,
        shortcode=shortcode,
        path_type=path_type,
    )

    return AddReelResponse(reel=reel, is_new=is_new, message=msg)


class UpdateReelRequest(BaseModel):
    title: Optional[str] = None
    note: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None


@vault_router.get("/api/vault/reels", response_model=VaultListResponse)
async def list_vault_reels(
    request: Request,
    query: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    sort: Optional[str] = "relevance",
    type: Optional[str] = None,
    needs_note: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Lists, searches, and filters saved reels in the personal vault with sorting options."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    reels, total, is_degraded, model_name = search_library(
        query=query,
        category=category,
        status=status,
        sort=sort,
        limit=limit,
        offset=offset,
        return_meta=True,
        item_type=type,
        needs_note=needs_note,
    )
    stats = get_vault_stats()
    return VaultListResponse(
        reels=reels,
        total_count=total,
        stats=stats,
        is_degraded_search=is_degraded,
        embedding_model=model_name,
        is_mock_pipeline=not settings.enable_real_pipeline,
    )


@vault_router.patch("/api/vault/reels/{shortcode}", response_model=VaultReel)
async def update_vault_reel(shortcode: str, payload: UpdateReelRequest, request: Request):
    """Updates user-editable metadata (title, note, category, tags) for a reel or post."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    from app.vault.database import update_reel_details
    updated = update_reel_details(
        shortcode=shortcode,
        title=payload.title,
        note=payload.note,
        category=payload.category,
        tags=payload.tags,
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"Reel or post '{shortcode}' not found.")
    return updated


@vault_router.get("/api/vault/reels/{shortcode}", response_model=VaultReel)
async def get_vault_reel(shortcode: str, request: Request):
    """Retrieves full details of an individual saved reel."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    reel = get_reel_by_shortcode(shortcode)
    if not reel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Reel '{shortcode}' not found in vault.")
    return reel


@vault_router.post("/api/vault/reels/{shortcode}/retry", response_model=RetryReelResponse)
async def retry_vault_reel(shortcode: str, request: Request):
    """Re-queues a failed or unavailable reel for re-processing."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    reel = retry_reel(shortcode)
    if not reel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Reel '{shortcode}' not found in vault.")

    return RetryReelResponse(
        shortcode=shortcode,
        status=reel.status,
        message="Reel has been re-queued for processing.",
        retry_count=reel.retry_count,
    )


@vault_router.post("/api/vault/reels/{shortcode}/reanalyze")
async def reanalyze_vault_reel(shortcode: str, request: Request, force_video: bool = True):
    """Forces re-analysis of a reel using low-res video to capture on-screen text and links."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    reel = get_reel_by_shortcode(shortcode)
    if not reel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Reel '{shortcode}' not found in vault.")

    from app.vault.worker import trigger_immediate_reanalysis
    await trigger_immediate_reanalysis(shortcode, force_video=force_video)

    return {
        "status": "processing",
        "shortcode": shortcode,
        "message": f"Video re-analysis started for reel '{shortcode}'.",
    }


@vault_router.post("/api/vault/reanalyze-unavailable")
async def reanalyze_unavailable_endpoint(request: Request):
    """Re-queues all reels currently marked as 'unavailable' or 'failed' for video re-analysis."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    from app.vault.database import reanalyze_unavailable_reels
    res = reanalyze_unavailable_reels()

    return {
        "status": "ok",
        "message": f"Queued {res['count']} unavailable/failed reel(s) for video re-analysis.",
        "count": res["count"],
        "shortcodes": res["shortcodes"],
    }


@vault_router.delete("/api/vault/reels/{shortcode}", response_model=DeleteReelResponse)
async def delete_vault_reel(shortcode: str, request: Request):
    """
    Safely removes a single Reel from the personal library, including search index and embeddings.
    Requires authentication.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    deleted = delete_reel(shortcode)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Reel '{shortcode}' not found in vault.")

    return DeleteReelResponse(
        shortcode=shortcode,
        status="deleted",
        message=f"Reel '{shortcode}' was successfully removed from your vault.",
    )


@vault_router.get("/api/vault/stats", response_model=VaultStats)
async def vault_statistics(request: Request):
    """Returns current vault totals, category counts, and daily cap usage."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return get_vault_stats()


@vault_router.post("/api/vault/import-legacy")
async def trigger_legacy_import(request: Request):
    """
    Imports and reconciles legacy Instagram Reels from recovered datasets into the library.
    Idempotent: safe to run repeatedly without creating duplicates.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return import_legacy_reels()


@vault_router.post("/api/vault/index-embeddings")
async def trigger_index_embeddings(request: Request, force: bool = False):
    """
    Indexes or re-indexes library reels into the SQLite embeddings table for semantic search.
    Idempotent: skips reels whose content hash is unchanged unless force=True.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")
    from app.vault.semantic import index_all_reels
    result = index_all_reels(force=force)
    return {"status": "ok", "result": result}


@vault_router.get("/api/vault/backup/export")
async def export_vault_backup(request: Request):
    """
    Exports the complete personal Reel Vault library as a downloadable JSON backup.
    Includes all Reel knowledge fields, original URLs, shortcode identities, statuses,
    timestamps, and user bookmarks.
    Never exports secrets, API keys, cookies, or runtime configuration.
    Requires authentication.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    backup_data = export_library_backup()
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"reel_vault_backup_{now_str}.json"
    return JSONResponse(
        content=backup_data,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@vault_router.post("/api/vault/backup/restore", response_model=BackupRestoreResponse)
async def restore_vault_backup(request: Request):
    """
    Restores or merges a previously exported library backup into the database.
    Accepts either JSON payload or multipart/form-data file upload.
    Validates structure before applying changes, merges idempotently, and never
    overwrites richer analyses with poorer data.
    Requires authentication.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")

    import json
    content_type = request.headers.get("content-type", "")
    backup_payload = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        file_obj = form.get("file")
        if not file_obj:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No backup file provided.")
        try:
            file_bytes = await file_obj.read()
            backup_payload = json.loads(file_bytes.decode("utf-8"))
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON file: {e}")
    else:
        try:
            backup_payload = await request.json()
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON payload: {e}")

    if not backup_payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty backup payload.")

    try:
        result = restore_library_backup(backup_payload)
        return BackupRestoreResponse(**result)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as ex:
        logger.exception("Restore operation failed: %s", ex)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Restore failed: {ex}")


@vault_router.post("/api/vault/reels/{shortcode}/bookmark", response_model=BookmarkResponse)
async def bookmark_vault_reel(shortcode: str, request: Request):
    """Toggles bookmark status on a saved Reel. Requires authentication."""
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required.")
    reel = toggle_bookmark(shortcode)
    if not reel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Reel '{shortcode}' not found in vault.")
    return BookmarkResponse(
        shortcode=shortcode,
        is_bookmarked=reel.is_bookmarked,
        message="Reel bookmarked." if reel.is_bookmarked else "Bookmark removed.",
    )



