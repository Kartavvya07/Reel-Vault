"""Server-rendered UI templates for Reel Vault personal knowledge library."""

import html
import json
from typing import Optional
from app.spike.config import settings
from app.vault.models import VaultReel, VaultStats


def render_login_page(error_message: Optional[str] = None, redirect_url: Optional[str] = None) -> str:
    """Renders the single-user vault unlock screen."""
    err_html = f'<div class="mb-5 p-3.5 rounded-lg bg-rose-50 border border-rose-200 text-rose-600 text-sm text-center font-medium">{html.escape(error_message)}</div>' if error_message else ''
    share_notice_html = ''
    if redirect_url and "share-target" in redirect_url:
        share_notice_html = '<div class="mb-5 p-3.5 rounded-lg bg-blue-50 border border-blue-200 text-blue-700 text-xs text-center font-medium">📱 Shared Reel received! Unlock your vault to save it.</div>'
    redirect_input = f'<input type="hidden" name="redirect" value="{html.escape(redirect_url)}">' if redirect_url else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reel Vault — Unlock</title>
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" type="image/svg+xml" href="/static/icons/icon.svg">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
        }};
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {{
            document.documentElement.classList.add('dark');
        }}
    </script>
</head>
<body class="min-h-screen flex items-center justify-center p-4 sm:p-8 bg-slate-50 dark:bg-slate-950 text-slate-800 dark:text-slate-100">
    <div class="w-full max-w-md bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-8 shadow-sm">
        <h1 class="text-2xl font-bold mb-2 text-slate-800 dark:text-white">Reel Vault</h1>
        <p class="text-xs text-slate-500 dark:text-slate-400 mb-6">Personal Knowledge Library</p>

        {share_notice_html}
        {err_html}

        <form method="POST" action="/login" class="space-y-4">
            {redirect_input}
            <div>
                <label class="block text-xs font-semibold text-slate-600 dark:text-slate-400 mb-2 uppercase tracking-wider">Access Token</label>
                <input type="password" name="token" required autofocus placeholder="Enter your vault key..." 
                       class="w-full p-3 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 text-slate-800 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">
            </div>
            <button type="submit" 
                    class="w-full py-3 px-4 rounded-lg bg-slate-900 hover:bg-slate-800 dark:bg-blue-600 dark:hover:bg-blue-500 text-white font-medium text-sm transition">
                Unlock Vault &rarr;
            </button>
        </form>
    </div>
</body>
</html>
"""


def render_main_vault_page() -> str:
    """Renders the main personal knowledge vault dashboard matching original index.html."""
    mock_banner_class = "" if not settings.enable_real_pipeline else "hidden"
    template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Reels Knowledge Base</title>
    <!-- PWA & Android Share Target Meta Tags -->
    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#f8fafc" media="(prefers-color-scheme: light)">
    <meta name="theme-color" content="#020617" media="(prefers-color-scheme: dark)">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="Reels Knowledge Base">
    <link rel="icon" type="image/svg+xml" href="/static/icons/icon.svg">
    <link rel="apple-touch-icon" href="/static/icons/icon-192.png">

    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
        };
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            document.documentElement.classList.add('dark');
        }
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
            if (e.matches) {
                document.documentElement.classList.add('dark');
            } else {
                document.documentElement.classList.remove('dark');
            }
        });
    </script>
    <style>
        * { box-sizing: border-box; }
        .line-clamp-4 {
            display: -webkit-box;
            -webkit-line-clamp: 4;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
    </style>
</head>
<body class="bg-slate-50 dark:bg-slate-950 min-h-screen p-4 sm:p-8 text-slate-800 dark:text-slate-100 overflow-x-hidden antialiased">
    <div class="max-w-6xl mx-auto w-full">

        <!-- Header: Title and Top Compact Actions -->
        <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
            <div>
                <h1 class="text-2xl sm:text-3xl font-bold text-slate-800 dark:text-white">Reels Knowledge Base</h1>
                <p class="text-xs text-slate-500 dark:text-slate-400 mt-0.5">Reel Vault Personal Library (<span id="resultsCount">0</span> reels)</p>
            </div>
            <div class="flex items-center gap-1.5 text-xs">
                <button id="pwaInstallBtn" class="hidden px-2.5 py-1.5 rounded-lg border border-blue-200 bg-blue-50 text-blue-700 font-medium">📱 Install</button>
                <button id="backupBtn" onclick="exportBackup()" class="px-2.5 py-1.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 font-medium hover:bg-slate-100">Backup</button>
                <button id="restoreBtn" onclick="openRestoreModal()" class="px-2.5 py-1.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 font-medium hover:bg-slate-100">Restore</button>
                <button id="importLegacyBtn" onclick="runLegacyImport()" class="hidden md:inline-block px-2.5 py-1.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-300 font-medium hover:bg-slate-100">Import</button>
                <a href="/logout" class="px-2.5 py-1.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-500 hover:text-rose-600 font-medium">Lock</a>
            </div>
        </div>

        <!-- Mock Mode Warning Banner -->
        <div id="mockModeBanner" class="__MOCK_BANNER_CLASS__ mb-6 p-4 rounded-2xl bg-rose-500/15 border border-rose-500/40 text-rose-700 dark:text-rose-300 text-xs sm:text-sm font-medium flex items-center justify-between shadow-sm">
            <div class="flex items-center gap-3">
                <span class="text-xl">🚨</span>
                <div>
                    <span class="font-bold text-rose-800 dark:text-rose-200">MOCK MODE ACTIVE:</span>
                    <span> Real Gemini pipeline is disabled (<code class="font-mono text-rose-700 dark:text-rose-300">ENABLE_REAL_PIPELINE=false</code>). Reel analysis will produce mock stubs!</span>
                </div>
            </div>
            <span class="px-2.5 py-1 rounded-full bg-rose-500/25 text-rose-800 dark:text-rose-200 border border-rose-500/40 font-mono text-[11px] font-bold tracking-wider">MOCK MODE</span>
        </div>

        <!-- Degraded Search Mode Banner -->
        <div id="degradedSearchBanner" class="hidden mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs font-medium flex items-center justify-between">
            <span>⚠️ <strong>Search degraded:</strong> Using local fallback embedder (<span id="degradedModelName">local-semantic-embedder</span>).</span>
            <span class="text-amber-600 font-mono text-[11px]">Degraded</span>
        </div>

        <!-- Offline Banner -->
        <div id="offlineBanner" class="hidden mb-4 p-3 rounded-lg bg-amber-50 border border-amber-200 text-amber-800 text-xs font-medium flex items-center justify-between">
            <span>⚠️ Connection interrupted. Saved data is safe.</span>
            <button onclick="fetchVaultData()" class="px-2.5 py-1 rounded bg-amber-200 text-amber-900 font-bold text-xs">Retry Now</button>
        </div>

        <!-- Android Share Sheet Feedback Banner -->
        <div id="shareAlertBanner" class="hidden mb-4 p-3 rounded-lg text-xs font-medium flex items-center justify-between border">
            <div class="flex items-center gap-2">
                <span id="shareAlertIcon">⚡</span>
                <span id="shareAlertText"></span>
            </div>
            <button onclick="document.getElementById('shareAlertBanner').classList.add('hidden')" class="text-slate-400 hover:text-slate-600 px-1">✕</button>
        </div>

        <!-- Add Reel (Compact & Prominent) -->
        <form id="addReelForm" class="flex gap-2 mb-4 w-full" title="Save Instagram Reel to Vault">
            <input type="url" id="reelUrlInput" required 
                   placeholder="Save Instagram link (reel, /p/, /tv/...)" 
                   title="Save Instagram Reel or Post to Vault"
                   class="flex-1 min-w-0 p-2.5 sm:p-3 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 text-xs sm:text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
            <button type="submit" id="submitBtn" title="Save Instagram Reel to Vault"
                    class="shrink-0 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2.5 sm:py-3 rounded-lg text-xs sm:text-sm font-semibold transition flex items-center gap-1.5 whitespace-nowrap">
                <span id="btnIcon">⚡</span>
                <span id="btnText">Save</span>
            </button>
        </form>
        <div id="submitFeedback" class="hidden mb-4 p-3 rounded-lg text-xs flex items-center justify-between gap-2 border"></div>

        <!-- Search Bar and Category Dropdown (Exact match to original dashboard) -->
        <div class="flex flex-col sm:flex-row gap-2.5 sm:gap-3 mb-6 w-full">
            <div class="relative flex-1 min-w-0">
                <input type="text" id="searchInput" placeholder="Search takeaways or tools..." 
                       class="w-full p-2.5 sm:p-3 pr-10 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
                <button id="clearInputBtn" onclick="clearSearchInput()" 
                        class="hidden absolute inset-y-0 right-0 pr-3 flex items-center text-slate-400 hover:text-slate-600 text-xs">✕</button>
                <span id="searchSpinner" class="hidden absolute inset-y-0 right-7 flex items-center pointer-events-none text-blue-500 text-xs animate-spin">⏳</span>
            </div>
            <div class="flex gap-2 w-full sm:w-auto min-w-0">
                <select id="categorySelect" onchange="onCategorySelect(this.value)" 
                        class="flex-1 sm:w-64 min-w-0 max-w-full p-2.5 sm:p-3 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer truncate">
                    <option value="all">All Categories</option>
                </select>
                <button id="filtersToggleBtn" onclick="toggleFiltersPanel()" 
                        class="shrink-0 px-3 py-2.5 sm:py-3 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-700 dark:text-slate-200 text-xs sm:text-sm font-medium hover:bg-slate-100 transition whitespace-nowrap">
                    <span>Filters</span> <span id="filtersChevron">▾</span>
                </button>
            </div>
        </div>

        <!-- Collapsible Secondary Filters -->
        <div id="filtersPanel" class="hidden p-4 rounded-xl bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 shadow-sm mb-6 space-y-3">
            <div class="flex flex-wrap items-center justify-between gap-3">
                <div class="flex items-center gap-1.5 overflow-x-auto" id="typeFilterBar">
                    <span class="text-xs text-slate-400 font-semibold mr-1">Type:</span>
                    <button data-type="all" class="type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800">All</button>
                    <button data-type="reels" class="type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800">🎬 Reels</button>
                    <button data-type="posts" class="type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800">📌 Posts</button>
                </div>
                <label class="flex items-center gap-1.5 text-xs text-slate-700 dark:text-slate-300 cursor-pointer font-medium select-none bg-slate-100 dark:bg-slate-950 px-2.5 py-1.5 rounded-lg border border-slate-200 dark:border-slate-800">
                    <input type="checkbox" id="needsNoteFilter" onchange="onNeedsNoteToggle(this.checked)" class="rounded text-blue-600 focus:ring-blue-500">
                    <span>⚠️ Needs note</span>
                </label>
            </div>
            <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-slate-100 dark:border-slate-800">
                <div class="flex items-center gap-1.5 overflow-x-auto" id="statusFilterBar">
                    <span class="text-xs text-slate-400 font-semibold mr-1">Status:</span>
                    <button data-status="all" class="status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800">All</button>
                    <button data-status="done" class="status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800">Done</button>
                    <button data-status="processing" class="status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800">In Progress</button>
                    <button data-status="failed" class="status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800">Failed</button>
                </div>
                <div class="flex items-center gap-2">
                    <label for="sortSelect" class="text-xs text-slate-500 font-semibold">Sort:</label>
                    <select id="sortSelect" onchange="onSortChange(this.value)" 
                            class="bg-slate-50 dark:bg-slate-950 text-xs font-semibold text-slate-700 dark:text-slate-200 border border-slate-300 dark:border-slate-700 rounded-lg px-2.5 py-1.5">
                        <option value="relevance">Relevance</option>
                        <option value="newest">Newest first</option>
                        <option value="oldest">Oldest first</option>
                        <option value="title">Title (A-Z)</option>
                    </select>
                </div>
            </div>
            <div class="flex items-center justify-between pt-2 border-t border-slate-100 dark:border-slate-800 text-xs text-slate-500">
                <div>Saved: <strong id="statTotal" class="text-slate-800 dark:text-white">0</strong> • Daily: <strong id="statDaily" class="text-blue-600">0/50</strong></div>
                <div class="flex items-center gap-2">
                    <button id="reanalyzeUnavailableBtn" onclick="reanalyzeUnavailable()" class="hidden px-2 py-1 rounded bg-amber-100 text-amber-800 font-medium">
                        🔄 Re-analyze Unavailable (<span id="unavailableCount">0</span>)
                    </button>
                    <button onclick="resetAllFilters()" class="underline hover:text-slate-800">Reset</button>
                </div>
            </div>
        </div>

        <!-- Processing Queue Tray -->
        <div id="queueTray" class="hidden mb-6 p-4 rounded-xl bg-amber-50 border border-amber-200 text-xs">
            <div class="font-bold text-amber-800 mb-2 flex items-center justify-between">
                <span>Active Queue (<span id="queueCount">0</span>)</span>
                <span class="text-amber-600 font-mono">Auto-refreshing...</span>
            </div>
            <div id="queueList" class="space-y-2"></div>
        </div>

        <!-- Grid: 1 col phone, 2 tablet, 3 desktop (matching original) -->
        <div id="libraryGrid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5 sm:gap-6 w-full">
            <!-- Cards injected dynamically -->
        </div>

        <!-- Empty State -->
        <div id="emptyState" class="hidden py-16 text-center bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-8 my-6">
            <h4 class="text-sm font-bold text-slate-800 dark:text-white mb-1" id="emptyStateTitle">No Reels Found</h4>
            <p class="text-xs text-slate-500 mb-4" id="emptyStateSubtitle">No records match your search or filter.</p>
            <button onclick="resetAllFilters()" class="px-4 py-2 rounded-lg bg-slate-100 text-xs font-semibold border">Clear Filters</button>
        </div>

        <!-- Compatibility anchor for category chips -->
        <div id="categoryChips" class="hidden"></div>
    </div>

    <!-- Detail Modal -->
    <div id="detailModal" class="fixed inset-0 z-50 hidden bg-slate-900/60 dark:bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
        <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 w-full max-w-2xl rounded-2xl p-6 sm:p-8 shadow-xl my-6 relative max-h-[90vh] overflow-y-auto">
            <button id="closeModalBtn" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-500 flex items-center justify-center text-sm font-bold">✕</button>
            <div id="modalContent"></div>
        </div>
    </div>

    <!-- Quick Note Modal (for manual posts) -->
    <div id="quickNoteModal" class="fixed inset-0 z-50 hidden bg-slate-900/60 dark:bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
        <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 w-full max-w-lg rounded-2xl p-6 sm:p-7 shadow-xl my-6 relative max-h-[90vh] overflow-y-auto">
            <button onclick="closeQuickNoteModal(true)" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-500 flex items-center justify-center text-sm font-bold">✕</button>
            <div class="flex items-center gap-2 mb-1">
                <span class="px-2 py-0.5 rounded text-[11px] font-bold bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">📌 Post Saved</span>
                <span id="quickPostShortcode" class="text-xs text-slate-400 font-mono"></span>
            </div>
            <h3 class="text-lg font-bold text-slate-900 dark:text-white mb-4">Add note for this post</h3>

            <!-- One-line What's this about? -->
            <div class="mb-4">
                <label for="quickNoteInput" class="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1.5">What's this about?</label>
                <input type="text" id="quickNoteInput" 
                       placeholder="e.g. Next.js 15 auth tutorial with cookies" 
                       class="w-full p-3 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 text-slate-800 dark:text-slate-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>

            <!-- Category Chips -->
            <div class="mb-4">
                <label class="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1.5">Category</label>
                <div id="quickCategoryChips" class="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto p-1">
                    <!-- Category chip buttons dynamically injected -->
                </div>
                <div id="quickCustomCategoryWrap" class="hidden mt-2">
                    <input type="text" id="quickCustomCategoryInput" placeholder="New category name..." 
                           class="w-full p-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 text-xs text-slate-800 dark:text-slate-100 focus:outline-none focus:ring-2 focus:ring-blue-500">
                </div>
            </div>

            <!-- Optional Tags -->
            <div class="mb-6">
                <label for="quickTagsInput" class="block text-xs font-bold text-slate-700 dark:text-slate-300 mb-1.5">Tags (optional)</label>
                <input type="text" id="quickTagsInput" 
                       placeholder="react, auth, tutorial (comma-separated)" 
                       class="w-full p-2.5 rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-950 text-slate-800 dark:text-slate-100 text-xs focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>

            <!-- Actions: One-tap Save & Skip -->
            <div class="flex items-center justify-between gap-3 pt-3 border-t border-slate-100 dark:border-slate-800">
                <button type="button" onclick="closeQuickNoteModal(true)" 
                        class="px-4 py-2.5 rounded-lg border border-slate-300 dark:border-slate-700 text-slate-600 dark:text-slate-400 text-xs font-semibold hover:bg-slate-100 dark:hover:bg-slate-800 transition">
                    Skip (Needs note)
                </button>
                <button type="button" onclick="submitQuickNote()" id="quickNoteSaveBtn"
                        class="flex-1 px-5 py-2.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs sm:text-sm transition flex items-center justify-center gap-1.5 shadow-sm">
                    <span>✓ Save Note</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Restore Modal -->
    <div id="restoreModal" class="fixed inset-0 z-50 hidden bg-slate-900/60 dark:bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
        <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 w-full max-w-lg rounded-2xl p-6 shadow-xl relative">
            <button onclick="closeRestoreModal()" class="absolute top-4 right-4 w-8 h-8 rounded-full bg-slate-100 text-slate-500 flex items-center justify-center text-sm font-bold">✕</button>
            <h3 class="text-base font-bold text-slate-900 dark:text-white mb-2">Restore Vault Library</h3>
            <p class="text-xs text-slate-600 dark:text-slate-400 mb-4">Upload a previously exported backup JSON file.</p>
            <div id="restoreDropzone" class="border-2 border-dashed border-slate-300 rounded-xl p-6 text-center cursor-pointer mb-4" onclick="document.getElementById('restoreFileInput').click()">
                <input type="file" id="restoreFileInput" accept=".json,application/json" class="hidden" onchange="handleRestoreFileSelected(event)">
                <div class="text-xs font-semibold text-slate-700 dark:text-slate-300" id="restoreFileLabel">Click to select backup JSON file</div>
            </div>
            <div id="restorePreviewBox" class="hidden mb-4 p-3 rounded-lg bg-slate-50 dark:bg-slate-950 border text-xs">
                <span class="text-slate-600">File: <strong id="previewFileName"></strong></span> (<span id="previewRecordCount" class="font-bold text-blue-600"></span>)
            </div>
            <div id="restoreErrorBox" class="hidden mb-4 p-3 rounded-lg bg-rose-50 text-rose-700 text-xs border border-rose-200"></div>
            <div id="restoreSuccessBox" class="hidden mb-4 p-3 rounded-lg bg-emerald-50 text-emerald-800 text-xs border border-emerald-200">
                <div class="font-bold mb-1">Restore Completed</div>
                <div id="restoreSuccessDetails"></div>
            </div>
            <div class="flex items-center justify-end gap-2 pt-2">
                <button type="button" onclick="closeRestoreModal()" class="px-4 py-2 rounded-lg border text-xs font-semibold">Cancel</button>
                <button type="button" id="confirmRestoreBtn" disabled onclick="executeRestore()" class="px-4 py-2 rounded-lg bg-slate-900 text-white disabled:opacity-40 text-xs font-bold">
                    <span id="restoreBtnSpinner" class="hidden animate-spin">⏳</span>
                    <span id="restoreBtnText">Confirm & Restore</span>
                </button>
            </div>
        </div>
    </div>

    <script>
        let allReels = [];
        let activeStatus = "all";
        let activeCategory = "all";
        let activeSort = "relevance";
        let activeType = "all";
        let activeNeedsNote = false;
        let availableCategories = [];
        let currentQuickShortcode = null;
        let selectedQuickCategory = null;
        let searchQuery = "";
        let pollTimer = null;
        let searchTimeout = null;

        function toggleFiltersPanel() {
            const panel = document.getElementById("filtersPanel");
            const chevron = document.getElementById("filtersChevron");
            if (!panel) return;
            const isHidden = panel.classList.contains("hidden");
            if (isHidden) {
                panel.classList.remove("hidden");
                if (chevron) chevron.textContent = "▴";
            } else {
                panel.classList.add("hidden");
                if (chevron) chevron.textContent = "▾";
            }
        }

        function onNeedsNoteToggle(checked) {
            activeNeedsNote = !!checked;
            fetchVaultData();
        }

        function openQuickNoteModal(shortcode) {
            currentQuickShortcode = shortcode;
            const reel = allReels.find(r => r.shortcode === shortcode);
            const scSpan = document.getElementById("quickPostShortcode");
            if (scSpan) scSpan.textContent = shortcode;
            const noteInput = document.getElementById("quickNoteInput");
            const tagsInput = document.getElementById("quickTagsInput");
            const customWrap = document.getElementById("quickCustomCategoryWrap");
            const customInput = document.getElementById("quickCustomCategoryInput");

            if (reel) {
                if (noteInput) noteInput.value = reel.takeaway || "";
                if (tagsInput) tagsInput.value = (reel.tags || []).join(", ");
                selectedQuickCategory = reel.category || null;
            } else {
                if (noteInput) noteInput.value = "";
                if (tagsInput) tagsInput.value = "";
                selectedQuickCategory = null;
            }

            if (customWrap) customWrap.classList.add("hidden");
            if (customInput) customInput.value = "";
            renderQuickCategoryChips();
            document.getElementById("quickNoteModal")?.classList.remove("hidden");
            setTimeout(() => { if (noteInput) noteInput.focus(); }, 100);
        }

        function renderQuickCategoryChips() {
            const wrap = document.getElementById("quickCategoryChips");
            if (!wrap) return;
            let chipsHtml = (availableCategories || []).map(cat => {
                const isSel = selectedQuickCategory === cat;
                const cls = isSel
                    ? "px-3 py-1.5 rounded-full text-xs font-semibold bg-blue-600 text-white border border-blue-600 transition"
                    : "px-3 py-1.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border border-slate-200 dark:border-slate-700 hover:border-blue-400 transition";
                return `<button type="button" onclick="selectQuickCategory('${escapeHtml(cat)}')" class="${cls}">${escapeHtml(cat)}</button>`;
            }).join("");

            const isNewSel = selectedQuickCategory === "__new__";
            const newCls = isNewSel
                ? "px-3 py-1.5 rounded-full text-xs font-semibold bg-blue-600 text-white border border-blue-600 transition"
                : "px-3 py-1.5 rounded-full text-xs font-medium bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300 border border-dashed border-slate-300 dark:border-slate-600 hover:border-blue-400 transition";
            chipsHtml += `<button type="button" onclick="selectQuickCustomCategory()" class="${newCls}">+ New category</button>`;
            wrap.innerHTML = chipsHtml;
        }

        function selectQuickCategory(cat) {
            selectedQuickCategory = cat;
            document.getElementById("quickCustomCategoryWrap")?.classList.add("hidden");
            renderQuickCategoryChips();
        }

        function selectQuickCustomCategory() {
            selectedQuickCategory = "__new__";
            const wrap = document.getElementById("quickCustomCategoryWrap");
            wrap?.classList.remove("hidden");
            renderQuickCategoryChips();
            document.getElementById("quickCustomCategoryInput")?.focus();
        }

        function closeQuickNoteModal(skip = false) {
            document.getElementById("quickNoteModal")?.classList.add("hidden");
            currentQuickShortcode = null;
            if (skip) {
                fetchVaultData();
            }
        }

        async function submitQuickNote() {
            if (!currentQuickShortcode) return;
            const saveBtn = document.getElementById("quickNoteSaveBtn");
            const note = document.getElementById("quickNoteInput")?.value.trim() || "";
            const tagsRaw = document.getElementById("quickTagsInput")?.value.trim() || "";
            const tags = tagsRaw ? tagsRaw.split(",").map(t => t.trim()).filter(Boolean) : [];

            let category = selectedQuickCategory;
            if (category === "__new__") {
                category = document.getElementById("quickCustomCategoryInput")?.value.trim() || null;
            }

            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.innerHTML = "<span>⏳ Saving...</span>";
            }

            try {
                const res = await fetch(`/api/vault/reels/${currentQuickShortcode}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        note: note,
                        category: category,
                        tags: tags
                    })
                });

                if (!res.ok) {
                    const err = await res.json();
                    alert("Failed to save note: " + (err.detail || "Unknown error"));
                } else {
                    closeQuickNoteModal(false);
                    fetchVaultData();
                }
            } catch (err) {
                alert("Error saving note: " + err.message);
            } finally {
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = "<span>✓ Save Note</span>";
                }
            }
        }

        async function saveReelEdits(shortcode) {
            const title = document.getElementById("editTitleInput")?.value.trim();
            const note = document.getElementById("editNoteInput")?.value.trim();
            const category = document.getElementById("editCategoryInput")?.value.trim();
            const tagsRaw = document.getElementById("editTagsInput")?.value.trim();
            const tags = tagsRaw ? tagsRaw.split(",").map(t => t.trim()).filter(Boolean) : [];
            const saveBtn = document.getElementById("saveEditsBtn");

            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.innerText = "Saving...";
            }

            try {
                const res = await fetch(`/api/vault/reels/${shortcode}`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        title: title,
                        note: note,
                        category: category,
                        tags: tags
                    })
                });

                if (!res.ok) {
                    const err = await res.json();
                    alert("Failed to save changes: " + (err.detail || "Unknown error"));
                } else {
                    const updated = await res.json();
                    const idx = allReels.findIndex(r => r.shortcode === shortcode);
                    if (idx !== -1) {
                        allReels[idx] = updated;
                    }
                    openDetailModal(shortcode);
                    renderLibrary(allReels);
                }
            } catch (err) {
                alert("Network error: " + err.message);
            } finally {
                if (saveBtn) {
                    saveBtn.disabled = false;
                    saveBtn.innerText = "✓ Save Changes";
                }
            }
        }

        async function copyToClipboard(text, btn) {
            try {
                await navigator.clipboard.writeText(text);
                const prev = btn.innerHTML;
                btn.innerHTML = "✓ Copied!";
                btn.classList.add("text-blue-600");
                setTimeout(() => {
                    btn.innerHTML = prev;
                    btn.classList.remove("text-blue-600");
                }, 1600);
            } catch (err) {
                prompt("Copy link:", text);
            }
        }

        async function deleteReel(shortcode, event) {
            if (event) event.stopPropagation();
            if (!confirm(`Are you sure you want to remove Reel '${shortcode}' from your personal vault?`)) {
                return;
            }
            try {
                const res = await fetch(`/api/vault/reels/${shortcode}`, { method: "DELETE" });
                if (res.ok) {
                    document.getElementById("detailModal")?.classList.add("hidden");
                    fetchVaultData();
                } else {
                    const err = await res.json();
                    alert(err.detail || "Failed to remove Reel.");
                }
            } catch (err) {
                alert("Network error deleting Reel: " + err.message);
            }
        }

        function exportBackup() {
            window.location.href = '/api/vault/backup/export';
        }

        function openRestoreModal() {
            const fileInput = document.getElementById("restoreFileInput");
            if (fileInput) fileInput.value = "";
            document.getElementById("restoreFileLabel").innerText = "Click to select backup JSON file";
            document.getElementById("restorePreviewBox")?.classList.add("hidden");
            document.getElementById("restoreErrorBox")?.classList.add("hidden");
            document.getElementById("restoreSuccessBox")?.classList.add("hidden");
            const btn = document.getElementById("confirmRestoreBtn");
            if (btn) {
                btn.disabled = true;
                btn.onclick = executeRestore;
            }
            document.getElementById("restoreBtnText").innerText = "Confirm & Restore";
            document.getElementById("restoreModal")?.classList.remove("hidden");
        }

        function closeRestoreModal() {
            document.getElementById("restoreModal")?.classList.add("hidden");
        }

        let selectedBackupPayload = null;
        function handleRestoreFileSelected(event) {
            const file = event.target.files[0];
            if (!file) return;

            document.getElementById("restoreErrorBox")?.classList.add("hidden");
            document.getElementById("restoreSuccessBox")?.classList.add("hidden");
            document.getElementById("restoreFileLabel").innerText = file.name;

            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const data = JSON.parse(e.target.result);
                    selectedBackupPayload = data;
                    const count = Array.isArray(data) ? data.length : (data.reels ? data.reels.length : 0);
                    document.getElementById("previewFileName").innerText = file.name;
                    document.getElementById("previewRecordCount").innerText = `${count} Reels`;
                    document.getElementById("restorePreviewBox")?.classList.remove("hidden");
                    const btn = document.getElementById("confirmRestoreBtn");
                    if (btn) btn.disabled = false;
                } catch (err) {
                    const errBox = document.getElementById("restoreErrorBox");
                    if (errBox) {
                        errBox.innerText = "Failed to parse JSON file: " + err.message;
                        errBox.classList.remove("hidden");
                    }
                    const btn = document.getElementById("confirmRestoreBtn");
                    if (btn) btn.disabled = true;
                }
            };
            reader.readAsText(file);
        }

        async function executeRestore() {
            if (!selectedBackupPayload) return;
            const btn = document.getElementById("confirmRestoreBtn");
            const spinner = document.getElementById("restoreBtnSpinner");
            const btnText = document.getElementById("restoreBtnText");
            const errorBox = document.getElementById("restoreErrorBox");
            const successBox = document.getElementById("restoreSuccessBox");
            const details = document.getElementById("restoreSuccessDetails");

            btn.disabled = true;
            spinner?.classList.remove("hidden");
            btnText.innerText = "Restoring...";
            errorBox?.classList.add("hidden");
            successBox?.classList.add("hidden");

            try {
                const res = await fetch("/api/vault/backup/restore", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(selectedBackupPayload),
                });

                const data = await res.json();
                if (!res.ok) {
                    errorBox.innerText = (data.error && data.error.message) ? data.error.message : (data.detail || "Restore failed.");
                    errorBox.classList.remove("hidden");
                    btn.disabled = false;
                    btnText.innerText = "Confirm & Restore";
                } else {
                    details.innerHTML = `
                        <div>• Imported: <strong>${data.imported_count}</strong></div>
                        <div>• Merged: <strong>${data.merged_count}</strong></div>
                        <div>• Skipped: <strong>${data.skipped_count}</strong></div>
                    `;
                    successBox?.classList.remove("hidden");
                    document.getElementById("restorePreviewBox")?.classList.add("hidden");
                    btnText.innerText = "Done";
                    btn.disabled = false;
                    btn.onclick = closeRestoreModal;
                    fetchVaultData();
                }
            } catch (err) {
                errorBox.innerText = "Network error: " + err.message;
                errorBox.classList.remove("hidden");
                btn.disabled = false;
                btnText.innerText = "Confirm & Restore";
            } finally {
                spinner?.classList.add("hidden");
            }
        }

        function initFromUrlParams() {
            const params = new URLSearchParams(window.location.search);
            const q = params.get("q") || params.get("query");
            const cat = params.get("category");
            const st = params.get("status");
            const sort = params.get("sort");
            const t = params.get("type");
            const nn = params.get("needs_note");

            if (q) {
                searchQuery = q.trim();
                const input = document.getElementById("searchInput");
                if (input) input.value = searchQuery;
                document.getElementById("clearInputBtn")?.classList.remove("hidden");
            }
            if (cat) {
                activeCategory = cat;
                const select = document.getElementById("categorySelect");
                if (select) select.value = cat;
            }
            if (st) activeStatus = st;
            if (sort) {
                activeSort = sort;
                const sortEl = document.getElementById("sortSelect");
                if (sortEl) sortEl.value = sort;
            }
            if (t) {
                activeType = t;
                document.querySelectorAll(".type-chip").forEach(btn => {
                    if (btn.dataset.type === t) {
                        btn.className = "type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800";
                    } else {
                        btn.className = "type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800";
                    }
                });
            }
            if (nn === "1" || nn === "true") {
                activeNeedsNote = true;
                const chk = document.getElementById("needsNoteFilter");
                if (chk) chk.checked = true;
            }
        }

        function updateUrlParams() {
            const params = new URLSearchParams();
            if (searchQuery) params.set("q", searchQuery);
            if (activeCategory !== "all") params.set("category", activeCategory);
            if (activeStatus !== "all") params.set("status", activeStatus);
            if (activeSort && activeSort !== "relevance") params.set("sort", activeSort);
            if (activeType && activeType !== "all") params.set("type", activeType);
            if (activeNeedsNote) params.set("needs_note", "true");

            const newUrl = params.toString() ? `${window.location.pathname}?${params.toString()}` : window.location.pathname;
            window.history.replaceState({}, document.title, newUrl);
        }

        async function fetchVaultData() {
            const spinner = document.getElementById("searchSpinner");
            const offlineBanner = document.getElementById("offlineBanner");
            if (spinner) spinner.classList.remove("hidden");

            try {
                const params = new URLSearchParams();
                if (searchQuery) params.append("query", searchQuery);
                if (activeCategory !== "all") params.append("category", activeCategory);
                if (activeStatus !== "all") params.append("status", activeStatus);
                if (activeSort) params.append("sort", activeSort);
                if (activeType && activeType !== "all") params.append("type", activeType);
                if (activeNeedsNote) params.append("needs_note", "true");

                const res = await fetch(`/api/vault/reels?${params.toString()}`);
                if (res.status === 401) {
                    window.location.href = "/login";
                    return;
                }
                const data = await res.json();
                allReels = data.reels || [];
                offlineBanner?.classList.add("hidden");

                const mockBanner = document.getElementById("mockModeBanner");
                if (data.is_mock_pipeline) {
                    mockBanner?.classList.remove("hidden");
                } else {
                    mockBanner?.classList.add("hidden");
                }

                const degBanner = document.getElementById("degradedSearchBanner");
                const degModel = document.getElementById("degradedModelName");
                if (data.is_degraded_search) {
                    degBanner?.classList.remove("hidden");
                    if (degModel && data.embedding_model) degModel.textContent = data.embedding_model;
                } else {
                    degBanner?.classList.add("hidden");
                }

                availableCategories = (data.stats && data.stats.categories) ? data.stats.categories : [];
                updateStats(data.stats);
                updateCategories(data.stats.categories || []);
                renderQueue(allReels);
                renderLibrary(allReels);
                updateUrlParams();
            } catch (err) {
                console.warn("Failed to fetch vault data:", err);
                offlineBanner?.classList.remove("hidden");
            } finally {
                if (spinner) spinner.classList.add("hidden");
            }
        }

        function updateStats(stats) {
            if (!stats) return;
            const totalEl = document.getElementById("statTotal");
            const dailyEl = document.getElementById("statDaily");
            const resultsEl = document.getElementById("resultsCount");
            if (totalEl) totalEl.textContent = stats.total_reels;
            if (dailyEl) dailyEl.textContent = `${stats.daily_processed_count}/${stats.daily_cap}`;
            if (resultsEl) resultsEl.textContent = allReels.length;

            const unavailBtn = document.getElementById("reanalyzeUnavailableBtn");
            const unavailCountSpan = document.getElementById("unavailableCount");
            if (unavailBtn && unavailCountSpan) {
                const count = stats.unavailable_count || 0;
                unavailCountSpan.textContent = count;
                if (count > 0) {
                    unavailBtn.classList.remove("hidden");
                } else {
                    unavailBtn.classList.add("hidden");
                }
            }
        }

        function updateCategories(categories) {
            const select = document.getElementById("categorySelect");
            if (!select) return;

            const currentVal = activeCategory;
            select.innerHTML = '<option value="all">All Categories</option>';
            categories.forEach(cat => {
                const opt = document.createElement("option");
                opt.value = cat;
                opt.textContent = cat;
                if (cat.toLowerCase() === currentVal.toLowerCase()) {
                    opt.selected = true;
                }
                select.appendChild(opt);
            });

            if (currentVal !== "all" && !categories.some(c => c.toLowerCase() === currentVal.toLowerCase())) {
                const opt = document.createElement("option");
                opt.value = currentVal;
                opt.textContent = currentVal;
                opt.selected = true;
                select.appendChild(opt);
            }
        }

        function onCategorySelect(val) {
            activeCategory = val;
            fetchVaultData();
        }

        function renderQueue(reels) {
            const queueItems = reels.filter(r => r.status === 'queued' || r.status === 'processing');
            const queueTray = document.getElementById("queueTray");
            const queueList = document.getElementById("queueList");
            const queueCount = document.getElementById("queueCount");

            if (queueItems.length === 0) {
                queueTray.classList.add("hidden");
                return;
            }

            queueTray.classList.remove("hidden");
            queueCount.textContent = queueItems.length;

            queueList.innerHTML = queueItems.map(item => `
                <div class="bg-white dark:bg-slate-900 border border-amber-200 rounded-lg p-2.5 flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <div class="w-2 h-2 rounded-full ${item.status === 'processing' ? 'bg-amber-500 animate-pulse' : 'bg-slate-400'}"></div>
                        <span class="font-bold text-slate-800 dark:text-white">${item.shortcode}</span>
                    </div>
                    <span class="text-[10px] font-bold uppercase ${item.status === 'processing' ? 'text-amber-700' : 'text-slate-500'}">${item.status}</span>
                </div>
            `).join('');
        }

        // Render Cards: Exact match to original dashboard
        function renderLibrary(reels) {
            const grid = document.getElementById("libraryGrid");
            const emptyState = document.getElementById("emptyState");

            const displayReels = reels.filter(r => r.status !== 'queued' && r.status !== 'processing');

            if (displayReels.length === 0) {
                grid.innerHTML = "";
                emptyState.classList.remove("hidden");
                const title = document.getElementById("emptyStateTitle");
                const subtitle = document.getElementById("emptyStateSubtitle");
                if (searchQuery) {
                    if (title) title.textContent = "No Good Matches";
                    if (subtitle) subtitle.textContent = `No records met the relevance threshold for "${searchQuery}".`;
                } else {
                    if (title) title.textContent = "No Records Found";
                    if (subtitle) subtitle.textContent = "No items found for the selected filter.";
                }
                return;
            }

            emptyState.classList.add("hidden");
            grid.innerHTML = displayReels.map(reel => {
                const isPost = reel.path_type === 'p' || reel.path_type === 'tv' || reel.path_type === 'post';
                const isDone = reel.status === 'done';
                const isFailed = reel.status === 'failed' || reel.status === 'unavailable';
                const isNotAnalyzed = reel.status === 'saved (not analyzed)' || reel.analysis_mode === 'not_analyzed';
                const needsNote = isPost && (!reel.takeaway || !reel.takeaway.trim());

                let typeBadge = '';
                if (isPost) {
                    typeBadge = '<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">📌 Post</span>';
                }

                let modeBadge = '';
                if (isNotAnalyzed) {
                    modeBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300 border border-slate-200 dark:border-slate-700">Not analyzed</span>';
                } else if (reel.analysis_mode === 'mock') {
                    modeBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] bg-rose-50 text-rose-700 border border-rose-200 font-bold" title="Produced by Mock Pipeline">🧪 Mock</span>';
                } else if (reel.analysis_mode === 'video') {
                    modeBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] bg-purple-50 text-purple-700 border border-purple-200 font-medium" title="Analyzed with multimodal video">🎬 Video</span>';
                } else if (reel.analysis_mode === 'images') {
                    modeBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 font-medium" title="Analyzed from post images">🖼️ Images</span>';
                } else if (reel.analysis_mode === 'caption_only') {
                    modeBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] bg-slate-100 text-slate-600 border border-slate-200 font-medium" title="Caption-only extraction">📝 Caption</span>';
                } else if (reel.analysis_mode === 'audio') {
                    modeBadge = '<span class="px-1.5 py-0.5 rounded text-[10px] bg-sky-50 text-sky-700 border border-sky-200 font-medium" title="Speech audio extraction">🎙️ Audio</span>';
                }

                let statusBadge = '';
                if (reel.relevance_score !== null && reel.relevance_score !== undefined && searchQuery) {
                    const pct = Math.round(reel.relevance_score * 100);
                    const matchType = reel.match_source === 'hybrid' ? 'Hybrid' : (reel.match_source === 'semantic' ? 'Semantic' : 'Keyword');
                    statusBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-50 text-blue-700 border border-blue-200 font-mono">${matchType} ${pct}%</span>`;
                } else if (needsNote) {
                    statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200">⚠️ Needs note</span>';
                } else if (isDone && reel.is_thin_extraction) {
                    statusBadge = '<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-50 text-amber-700 border border-amber-200">⚠️ Limited</span>';
                } else if (isFailed) {
                    statusBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-rose-50 text-rose-700 border border-rose-200">${reel.status}</span>`;
                }

                // Format tools list: "Tools: Uiverse, Anime.js, shadcn/ui"
                let toolNames = [];
                if (reel.tools && Array.isArray(reel.tools)) {
                    toolNames = reel.tools.map(t => t.name).filter(Boolean);
                }
                const toolsString = toolNames.length > 0 ? toolNames.join(', ') : (isPost ? 'None (manual entry)' : 'None');

                const snippetHtml = (reel.match_snippet && searchQuery) ? `
                    <div class="mt-3 p-2.5 rounded-lg bg-blue-50 border border-blue-200 text-xs text-blue-900 leading-relaxed">
                        <span class="font-semibold text-blue-700">🔍 Match:</span> ${escapeHtml(reel.match_snippet)}
                    </div>
                ` : '';

                // Main card content: user note for posts, core takeaway for reels
                let mainContentHtml = '';
                if (isPost && (!reel.takeaway || !reel.takeaway.trim())) {
                    mainContentHtml = '<span class="text-amber-700 dark:text-amber-400 font-semibold italic">⚠️ Needs note &mdash; tap to describe what this post is about</span>';
                } else {
                    mainContentHtml = escapeHtml(reel.takeaway || reel.error_message || (isPost ? 'Post saved without note.' : 'Processing completed.'));
                }

                const actionButtonText = isPost ? 'Open Post &rarr;' : 'Watch Reel &rarr;';

                return `
                <div class="bg-white dark:bg-slate-900 p-5 sm:p-6 rounded-xl shadow-sm border border-slate-200 dark:border-slate-800 hover:shadow-md transition flex flex-col justify-between cursor-pointer" onclick="openDetailModal('${reel.shortcode}')">
                    <div>
                        <!-- Category Pill (Top Row) -->
                        <div class="mb-4 flex items-center justify-between gap-2 flex-wrap">
                            <span class="text-xs font-semibold px-2.5 py-1 bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 rounded-full cursor-pointer hover:opacity-85" onclick="event.stopPropagation(); filterByCategory('${escapeHtml(reel.category || 'Uncategorized')}')">
                                ${escapeHtml(reel.category || 'Uncategorized')}
                            </span>
                            <div class="flex items-center gap-1.5 flex-wrap">
                                ${typeBadge}
                                ${modeBadge}
                                ${statusBadge}
                            </div>
                        </div>

                        <!-- Takeaway / Note: Dominant Content -->
                        <p class="text-slate-700 dark:text-slate-200 text-sm font-medium leading-relaxed flex-grow line-clamp-4">
                            ${mainContentHtml}
                        </p>

                        ${snippetHtml}

                        <!-- Tools Row -->
                        <div class="mt-4 text-xs text-slate-500 dark:text-slate-400">
                            <span class="font-semibold text-slate-700 dark:text-slate-300">Tools:</span> ${escapeHtml(toolsString)}
                        </div>
                    </div>

                    <!-- Watch Reel / Open Post Button (Bottom Row) -->
                    <a href="${escapeHtml(reel.original_url)}" target="_blank" rel="noopener noreferrer" 
                       onclick="event.stopPropagation();" 
                       class="mt-6 inline-block w-full text-center bg-slate-900 hover:bg-slate-800 dark:bg-slate-800 dark:hover:bg-slate-700 text-white py-2.5 rounded-lg text-sm font-medium transition">
                        ${actionButtonText}
                    </a>
                </div>
                `;
            }).join('');
        }

        function openDetailModal(shortcode) {
            const reel = allReels.find(r => r.shortcode === shortcode);
            if (!reel) return;

            const modal = document.getElementById("detailModal");
            const content = document.getElementById("modalContent");
            const isPost = reel.path_type === 'p' || reel.path_type === 'tv' || reel.path_type === 'post';
            const isNotAnalyzed = reel.status === 'saved (not analyzed)' || reel.analysis_mode === 'not_analyzed';
            const needsNote = isPost && (!reel.takeaway || !reel.takeaway.trim());

            const toolsHtml = (reel.tools || []).map(t => {
                const isVerified = t.verification_status === 'verified';
                const verifyBadge = isVerified 
                    ? '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">✓ Verified</span>'
                    : '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-50 text-amber-700 border border-amber-200">Extracted (Unverified)</span>';

                return `
                <div class="p-3 rounded-lg bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs">
                    <div>
                        <div class="flex items-center gap-1.5">
                            <span class="font-bold text-slate-800 dark:text-white">${escapeHtml(t.name)}</span>
                            ${verifyBadge}
                        </div>
                        <span class="text-[10px] text-slate-500 font-mono">Type: ${escapeHtml(t.type || 'other')} • Source: ${escapeHtml(t.source || 'speech')}</span>
                    </div>
                    <div class="flex items-center gap-2">
                        ${t.url ? `<button onclick="copyToClipboard('${escapeHtml(t.url)}', this)" class="text-xs text-slate-500 hover:text-slate-800">Copy</button>` : ''}
                        ${t.url ? `<a href="${escapeHtml(t.url)}" target="_blank" rel="noopener noreferrer" class="text-xs text-blue-600 hover:underline">Link &rarr;</a>` : ''}
                    </div>
                </div>
                `;
            }).join('');

            const extractedUrlsHtml = (reel.extracted_urls || []).map(u => `
                <div class="p-2.5 rounded-lg bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 flex items-center justify-between text-xs">
                    <div class="truncate max-w-sm">
                        <span class="text-slate-700 dark:text-slate-300 font-mono truncate block">${escapeHtml(u.url)}</span>
                        <span class="text-[10px] text-amber-700">⚠️ Extracted link &mdash; unverified source (${escapeHtml(u.source || 'caption')})</span>
                    </div>
                    <div class="flex items-center gap-2 ml-2">
                        <button onclick="copyToClipboard('${escapeHtml(u.url)}', this)" class="text-xs text-slate-500 hover:text-slate-800">Copy</button>
                        <a href="${escapeHtml(u.url)}" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">Open &rarr;</a>
                    </div>
                </div>
            `).join('');

            const keyPointsHtml = (reel.key_points || []).map((pt, idx) => `
                <li class="flex items-start gap-2 text-xs text-slate-700 dark:text-slate-300 leading-relaxed">
                    <span class="text-blue-600 font-bold font-mono">${idx + 1}.</span>
                    <span>${escapeHtml(pt)}</span>
                </li>
            `).join('');

            const codeSnippetsHtml = (reel.code_snippets || []).map(cs => `
                <div class="p-3 rounded-lg bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 font-mono text-xs text-blue-700 mb-2 overflow-x-auto">
                    ${cs.language_or_shell ? `<span class="text-[10px] text-slate-400 block mb-1"># ${escapeHtml(cs.language_or_shell)}</span>` : ''}
                    <code>${escapeHtml(cs.code || '')}</code>
                </div>
            `).join('');

            content.innerHTML = `
                <div class="mb-4">
                    <div class="flex items-center gap-2 mb-2 flex-wrap">
                        <span class="text-xs font-semibold px-2.5 py-1 bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 rounded-full">
                            ${escapeHtml(reel.category || 'General')}
                        </span>
                        ${isPost ? '<span class="text-xs font-bold px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">📌 Post</span>' : ''}
                        ${isNotAnalyzed ? '<span class="text-xs font-medium px-2 py-0.5 rounded bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300 border border-slate-200">Not analyzed</span>' : (reel.analysis_mode === 'mock' ? '<span class="text-xs font-bold px-2.5 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200">🧪 Mock Data</span>' : (reel.analysis_mode === 'video' ? '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-purple-50 text-purple-700 border border-purple-200">🎬 Video</span>' : (reel.analysis_mode === 'images' ? '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">🖼️ Images</span>' : (reel.analysis_mode === 'caption_only' ? '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">📝 Caption</span>' : '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-200">🎙️ Audio</span>'))))}
                        ${needsNote ? '<span class="text-xs font-bold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">⚠️ Needs note</span>' : ''}
                        ${reel.is_thin_extraction ? '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">⚠️ Limited Extraction</span>' : ''}
                        <span class="text-xs text-slate-500 font-mono">${reel.shortcode}</span>
                    </div>
                    <h2 class="text-xl font-bold text-slate-900 dark:text-white mb-3">
                        ${escapeHtml(reel.title || (isPost ? 'Post ' + reel.shortcode : 'Reel ' + reel.shortcode))}
                    </h2>
                    
                    <!-- Prominent Original Link Button -->
                    <div class="flex items-center gap-2 mb-5">
                        <a href="${escapeHtml(reel.original_url)}" target="_blank" rel="noopener noreferrer" 
                           class="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg bg-slate-900 hover:bg-slate-800 dark:bg-slate-800 dark:hover:bg-slate-700 text-white text-xs font-semibold transition">
                            <span>${isPost ? '📌 Open Post on Instagram' : '🎬 Watch Reel on Instagram'}</span> &rarr;
                        </a>
                        <button onclick="copyToClipboard('${escapeHtml(reel.original_url)}', this)" 
                                class="px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-slate-800 text-slate-600 dark:text-slate-300 text-xs font-mono transition">
                            📋 Copy URL
                        </button>
                    </div>
                </div>

                ${reel.match_snippet ? `
                <div class="p-4 rounded-xl bg-blue-50 border border-blue-200 mb-4 text-xs">
                    <h3 class="font-bold text-blue-700 mb-1">Search Match Reason</h3>
                    <p class="text-blue-900 leading-relaxed">${escapeHtml(reel.match_snippet)}</p>
                </div>
                ` : ''}

                <!-- Edit Details Form (Title, Note, Category, Tags) -->
                <div class="p-4 sm:p-5 rounded-xl bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 mb-5">
                    <div class="flex items-center justify-between mb-3">
                        <h3 class="text-xs font-bold uppercase text-slate-500">Edit Details</h3>
                        <span class="text-[11px] text-slate-400">Save updates instantly</span>
                    </div>
                    <div class="space-y-3">
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 dark:text-slate-400 mb-1">Title</label>
                            <input type="text" id="editTitleInput" value="${escapeHtml(reel.title || '')}" 
                                   class="w-full p-2.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
                        </div>
                        <div>
                            <label class="block text-[11px] font-bold text-slate-600 dark:text-slate-400 mb-1">What's this about? (Note / Takeaway)</label>
                            <textarea id="editNoteInput" rows="3" placeholder="Describe the takeaway or note..." 
                                      class="w-full p-2.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-white leading-relaxed focus:outline-none focus:ring-2 focus:ring-blue-500">${escapeHtml(reel.takeaway || '')}</textarea>
                        </div>
                        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                            <div>
                                <label class="block text-[11px] font-bold text-slate-600 dark:text-slate-400 mb-1">Category</label>
                                <input type="text" id="editCategoryInput" value="${escapeHtml(reel.category || '')}" placeholder="e.g. Front-End, UI/UX" 
                                       class="w-full p-2.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
                            </div>
                            <div>
                                <label class="block text-[11px] font-bold text-slate-600 dark:text-slate-400 mb-1">Tags (comma-separated)</label>
                                <input type="text" id="editTagsInput" value="${escapeHtml((reel.tags || []).join(', '))}" placeholder="e.g. react, ui, css" 
                                       class="w-full p-2.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
                            </div>
                        </div>
                        <div class="flex justify-end pt-2">
                            <button id="saveEditsBtn" onclick="saveReelEdits('${reel.shortcode}')" 
                                    class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs transition">
                                ✓ Save Changes
                            </button>
                        </div>
                    </div>
                </div>

                <!-- Key Points -->
                ${reel.key_points && reel.key_points.length ? `
                <div class="mb-4">
                    <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Key Points</h3>
                    <ul class="space-y-1.5 bg-slate-50 dark:bg-slate-950 p-4 rounded-xl border border-slate-200 dark:border-slate-800">
                        ${keyPointsHtml}
                    </ul>
                </div>` : ''}

                <!-- Tools -->
                ${reel.tools && reel.tools.length ? `
                <div class="mb-4">
                    <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Tools & Resources</h3>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        ${toolsHtml}
                    </div>
                </div>` : ''}

                <!-- Extracted URLs -->
                ${reel.extracted_urls && reel.extracted_urls.length ? `
                <div class="mb-4">
                    <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Extracted External Links (Unverified)</h3>
                    <div class="space-y-2">
                        ${extractedUrlsHtml}
                    </div>
                </div>` : ''}

                <!-- Code Snippets -->
                ${reel.code_snippets && reel.code_snippets.length ? `
                <div class="mb-4">
                    <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Code Snippets</h3>
                    ${codeSnippetsHtml}
                </div>` : ''}

                <!-- Transcript / Visual -->
                ${reel.transcript ? `
                <div class="mb-4">
                    <div class="flex items-center justify-between mb-1">
                        <h3 class="text-xs font-bold uppercase text-slate-400">Speech Transcript</h3>
                        <button onclick="navigator.clipboard.writeText(document.getElementById('transcriptBlock').innerText); alert('Transcript copied!');" class="text-[11px] text-slate-500 hover:text-slate-800">Copy</button>
                    </div>
                    <div id="transcriptBlock" class="p-3 rounded-lg bg-slate-50 dark:bg-slate-950 border border-slate-200 text-xs text-slate-700 font-mono max-h-36 overflow-y-auto">
                        ${escapeHtml(reel.transcript)}
                    </div>
                </div>` : ''}

                <!-- Footer buttons -->
                <div class="flex items-center justify-between pt-4 border-t border-slate-200 dark:border-slate-800 flex-wrap gap-2">
                    <div class="flex items-center gap-2">
                        <a href="/reels/${reel.shortcode}" class="text-xs text-blue-600 hover:underline">Standalone Page &rarr;</a>
                        <button onclick="deleteReel('${reel.shortcode}')" class="px-3 py-1.5 rounded-lg border border-rose-200 text-rose-600 hover:bg-rose-50 text-xs font-semibold">🗑️ Delete</button>
                    </div>
                    <div class="flex items-center gap-2">
                        ${!isPost ? `
                            <button onclick="reanalyzeReel('${reel.shortcode}')" class="px-3 py-1.5 rounded-lg bg-purple-50 text-purple-700 border border-purple-200 font-semibold text-xs">🎬 Re-analyze</button>
                        ` : ''}
                        ${(reel.status === 'failed' || reel.status === 'unavailable') ? `
                            <button onclick="retryReel('${reel.shortcode}')" class="px-4 py-1.5 rounded-lg bg-rose-600 text-white font-bold text-xs">Retry</button>
                        ` : ''}
                    </div>
                </div>
            `;

            modal.classList.remove("hidden");
        }

        async function reanalyzeReel(shortcode) {
            if (!confirm("Re-analyze this reel using multimodal video extraction?")) return;
            try {
                const res = await fetch(`/api/vault/reels/${shortcode}/reanalyze?force_video=true`, { method: "POST" });
                const data = await res.json();
                alert(data.message || "Re-analysis started in background!");
                document.getElementById("detailModal")?.classList.add("hidden");
                fetchVaultData();
            } catch (err) {
                alert("Failed to start re-analysis: " + err.message);
            }
        }

        async function reanalyzeUnavailable() {
            const count = document.getElementById("unavailableCount")?.textContent || "all";
            if (!confirm(`Re-analyze ${count} unavailable reels using adaptive video extraction?`)) return;
            const btn = document.getElementById("reanalyzeUnavailableBtn");
            if (btn) {
                btn.disabled = true;
                btn.textContent = "⏳ Queueing...";
            }
            try {
                const res = await fetch("/api/vault/reanalyze-unavailable", { method: "POST" });
                const data = await res.json();
                alert(data.message || "Queued unavailable reels for re-analysis!");
                fetchVaultData();
            } catch (err) {
                alert("Failed to trigger re-analysis: " + err.message);
            } finally {
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = `🔄 Re-analyze Unavailable (<span id="unavailableCount">0</span>)`;
                }
                fetchVaultData();
            }
        }

        document.getElementById("closeModalBtn").onclick = () => {
            document.getElementById("detailModal").classList.add("hidden");
        };

        function filterByCategory(cat) {
            activeCategory = cat;
            const select = document.getElementById("categorySelect");
            if (select) select.value = cat;
            fetchVaultData();
        }

        function filterByTag(tag) {
            searchQuery = tag;
            const input = document.getElementById("searchInput");
            if (input) input.value = tag;
            document.getElementById("clearInputBtn")?.classList.remove("hidden");
            fetchVaultData();
        }

        function onSortChange(sortVal) {
            activeSort = sortVal;
            fetchVaultData();
        }

        function clearSearchInput() {
            searchQuery = "";
            const input = document.getElementById("searchInput");
            if (input) input.value = "";
            document.getElementById("clearInputBtn")?.classList.add("hidden");
            fetchVaultData();
        }

        function resetAllFilters() {
            searchQuery = "";
            activeCategory = "all";
            activeStatus = "all";
            activeSort = "relevance";
            activeType = "all";
            activeNeedsNote = false;
            const input = document.getElementById("searchInput");
            if (input) input.value = "";
            document.getElementById("clearInputBtn")?.classList.add("hidden");
            const sortEl = document.getElementById("sortSelect");
            if (sortEl) sortEl.value = "relevance";
            const catEl = document.getElementById("categorySelect");
            if (catEl) catEl.value = "all";
            const chk = document.getElementById("needsNoteFilter");
            if (chk) chk.checked = false;

            document.querySelectorAll(".type-chip").forEach(c => {
                c.className = c.dataset.type === "all"
                    ? "type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800"
                    : "type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800";
            });

            document.querySelectorAll(".status-chip").forEach(c => {
                c.className = c.dataset.status === "all"
                    ? "status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800"
                    : "status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800";
            });
            fetchVaultData();
        }

        async function retryReel(shortcode) {
            try {
                const res = await fetch(`/api/vault/reels/${shortcode}/retry`, { method: "POST" });
                if (res.ok) {
                    fetchVaultData();
                }
            } catch (err) {
                console.error("Retry failed:", err);
            }
        }

        async function runLegacyImport() {
            const btn = document.getElementById("importLegacyBtn");
            const feedback = document.getElementById("submitFeedback");
            btn.disabled = true;
            btn.classList.add("opacity-60");
            btn.innerHTML = "<span>⏳</span> <span>Importing...</span>";

            try {
                const res = await fetch("/api/vault/import-legacy", { method: "POST" });
                const data = await res.json();
                feedback.classList.remove("hidden", "bg-rose-50", "text-rose-700", "border-rose-200");
                feedback.classList.add("bg-emerald-50", "text-emerald-800", "border-emerald-200");
                feedback.textContent = `✓ Legacy Import: ${data.imported_count} new imported, ${data.merged_count} merged, ${data.incomplete_count} incomplete flagged, ${data.skipped_count} skipped. Total: ${data.total_vault_reels}`;
                fetchVaultData();
            } catch (err) {
                alert("Import failed: " + err.message);
            } finally {
                btn.disabled = false;
                btn.classList.remove("opacity-60");
                btn.innerHTML = "<span>📥</span> <span>Import</span>";
            }
        }

        document.getElementById("addReelForm").onsubmit = async (e) => {
            e.preventDefault();
            const input = document.getElementById("reelUrlInput");
            const btn = document.getElementById("submitBtn");
            const feedback = document.getElementById("submitFeedback");

            const url = input.value.trim();
            if (!url) return;

            btn.disabled = true;
            btn.classList.add("opacity-60");

            try {
                const res = await fetch("/api/vault/reels", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url: url })
                });

                const data = await res.json();
                feedback.classList.remove("hidden", "bg-rose-50", "text-rose-700", "border-rose-200", "bg-emerald-50", "text-emerald-800", "border-emerald-200");

                if (res.ok) {
                    feedback.classList.add("bg-emerald-50", "text-emerald-800", "border-emerald-200");
                    const isPost = data.reel && (data.reel.path_type === 'p' || data.reel.path_type === 'tv' || data.reel.path_type === 'post');
                    if (data.is_new) {
                        feedback.textContent = `✓ ${data.message}`;
                    } else {
                        feedback.innerHTML = `<span>✓ ${escapeHtml(data.message)}</span> <button onclick="openDetailModal('${data.reel.shortcode}')" class="ml-2 underline font-bold">View Card &rarr;</button>`;
                    }
                    input.value = "";
                    fetchVaultData();
                    if (isPost) {
                        setTimeout(() => {
                            openQuickNoteModal(data.reel.shortcode);
                        }, 250);
                    }
                } else {
                    feedback.classList.add("bg-rose-50", "text-rose-700", "border-rose-200");
                    feedback.textContent = `✕ ${data.detail || data.error?.message || "Failed to add reel."}`;
                }
            } catch (err) {
                feedback.classList.remove("hidden");
                feedback.classList.add("bg-rose-50", "text-rose-700", "border-rose-200");
                feedback.textContent = `✕ Server error: ${err.message}`;
            } finally {
                btn.disabled = false;
                btn.classList.remove("opacity-60");
            }
        };

        document.querySelectorAll(".type-chip").forEach(btn => {
            btn.onclick = () => {
                activeType = btn.dataset.type;
                document.querySelectorAll(".type-chip").forEach(c => {
                    c.className = "type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800";
                });
                btn.className = "type-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800";
                fetchVaultData();
            };
        });

        document.querySelectorAll(".status-chip").forEach(btn => {
            btn.onclick = () => {
                activeStatus = btn.dataset.status;
                document.querySelectorAll(".status-chip").forEach(c => {
                    c.className = "status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-600 dark:bg-slate-950 dark:text-slate-400 border border-slate-200 dark:border-slate-800";
                });
                btn.className = "status-chip px-3 py-1.5 rounded-lg text-xs font-semibold bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200 dark:border-blue-800";
                fetchVaultData();
            };
        });

        document.getElementById("searchInput").oninput = (e) => {
            const val = e.target.value;
            const clearBtn = document.getElementById("clearInputBtn");
            if (val.trim()) {
                clearBtn?.classList.remove("hidden");
            } else {
                clearBtn?.classList.add("hidden");
            }

            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                searchQuery = val.trim();
                fetchVaultData();
            }, 300);
        };

        function escapeHtml(str) {
            if (!str) return '';
            return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        }

        pollTimer = setInterval(fetchVaultData, 4000);

        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js').catch(err => {
                    console.warn('SW registration error:', err);
                });
            });
        }

        let deferredInstallPrompt = null;
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredInstallPrompt = e;
            const btn = document.getElementById('pwaInstallBtn');
            if (btn) btn.classList.remove('hidden');
        });

        document.getElementById('pwaInstallBtn')?.addEventListener('click', async () => {
            if (deferredInstallPrompt) {
                deferredInstallPrompt.prompt();
                const { outcome } = await deferredInstallPrompt.userChoice;
                if (outcome === 'accepted') {
                    document.getElementById('pwaInstallBtn')?.classList.add('hidden');
                }
                deferredInstallPrompt = null;
            } else {
                alert("To install Reel Vault on Android:\\n1. Open in Chrome.\\n2. Tap the ⋮ menu in the top-right.\\n3. Tap 'Install app' or 'Add to Home screen'.");
            }
        });

        function checkShareParams() {
            const params = new URLSearchParams(window.location.search);
            const sharedCode = params.get('shared');
            const shareError = params.get('share_error');
            const openQuick = params.get('open_quick_note') === '1';
            const banner = document.getElementById('shareAlertBanner');
            const icon = document.getElementById('shareAlertIcon');
            const text = document.getElementById('shareAlertText');

            if (sharedCode && banner) {
                const isNew = params.get('is_new') === '1';
                banner.classList.remove('hidden', 'bg-rose-50', 'text-rose-700', 'border-rose-200', 'bg-blue-50', 'text-blue-700', 'border-blue-200');
                if (isNew) {
                    banner.classList.add('bg-blue-50', 'text-blue-700', 'border-blue-200');
                    icon.textContent = openQuick ? '📌' : '⚡';
                    text.innerHTML = openQuick 
                        ? `<strong>Shared Post Saved:</strong> Post ${escapeHtml(sharedCode)} saved! <button onclick="openQuickNoteModal('${escapeHtml(sharedCode)}')" class="ml-2 underline font-bold">Add Note &rarr;</button>`
                        : `<strong>Shared Reel Queued:</strong> Reel ${escapeHtml(sharedCode)} added to queue!`;
                } else {
                    banner.classList.add('bg-amber-50', 'text-amber-800', 'border-amber-200');
                    icon.textContent = '📌';
                    text.innerHTML = `<strong>Already Saved:</strong> ${escapeHtml(sharedCode)} is already in your vault! <button onclick="openDetailModal('${escapeHtml(sharedCode)}')" class="ml-2 underline font-bold">Open Card &rarr;</button>`;
                }
                if (openQuick) {
                    setTimeout(() => {
                        openQuickNoteModal(sharedCode);
                    }, 400);
                }
                window.history.replaceState({}, document.title, window.location.pathname);
            } else if (shareError && banner) {
                banner.classList.remove('hidden', 'bg-blue-50', 'text-blue-700', 'border-blue-200');
                banner.classList.add('bg-rose-50', 'text-rose-700', 'border-rose-200');
                icon.textContent = '⚠️';
                text.innerHTML = `<strong>Share Notice:</strong> ${escapeHtml(shareError)}`;
                window.history.replaceState({}, document.title, window.location.pathname);
            }
        }

        initFromUrlParams();
        checkShareParams();
        fetchVaultData();
    </script>
</body>
</html>
"""
    return template.replace("__MOCK_BANNER_CLASS__", mock_banner_class)


def render_reel_detail_page(reel: VaultReel) -> str:
    """Renders a standalone, bookmarkable detail page for a single reel."""
    tools_items = []
    for t in reel.tools:
        name_esc = html.escape(t.name)
        type_esc = html.escape(t.type or "other")
        source_esc = html.escape(t.source or "speech")
        status_badge = (
            '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">✓ Verified</span>'
            if t.verification_status == "verified"
            else '<span class="px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-50 text-amber-700 border border-amber-200">Extracted (Unverified)</span>'
        )
        url_actions = ""
        if t.url:
            url_esc = html.escape(t.url)
            url_actions = f"""
                <button onclick="navigator.clipboard.writeText('{url_esc}'); alert('Link copied!');" class="text-xs text-slate-500 hover:text-slate-900">Copy</button>
                <a href="{url_esc}" target="_blank" rel="noopener noreferrer" class="text-xs text-blue-600 hover:underline">Link &rarr;</a>
            """
        tools_items.append(f"""
        <div class="p-3.5 rounded-lg bg-slate-50 border border-slate-200 flex items-center justify-between">
            <div>
                <div class="flex items-center gap-1.5">
                    <span class="font-bold text-slate-900 text-xs">{name_esc}</span>
                    {status_badge}
                </div>
                <span class="text-[10px] text-slate-500 font-mono">Type: {type_esc} • Source: {source_esc}</span>
            </div>
            <div class="flex items-center gap-2">
                {url_actions}
            </div>
        </div>
        """)
    tools_html = "".join(tools_items)

    extracted_urls_html = "".join([f"""
        <div class="p-3 rounded-lg bg-slate-50 border border-slate-200 flex items-center justify-between text-xs">
            <div class="truncate max-w-sm">
                <span class="text-slate-700 font-mono truncate block">{html.escape(u.url)}</span>
                <span class="text-[10px] text-amber-700">⚠️ Extracted link &mdash; unverified source ({html.escape(u.source or 'caption')})</span>
            </div>
            <div class="flex items-center gap-2 ml-2">
                <button onclick="navigator.clipboard.writeText('{html.escape(u.url)}'); alert('Link copied!');" class="text-xs text-slate-500 hover:text-slate-900">Copy</button>
                <a href="{html.escape(u.url)}" target="_blank" rel="noopener noreferrer" class="text-blue-600 hover:underline">Open &rarr;</a>
            </div>
        </div>
    """ for u in reel.extracted_urls])

    points_html = "".join([f"""
        <li class="flex items-start gap-2.5 text-xs text-slate-700 leading-relaxed">
            <span class="text-blue-600 font-bold mono">{i + 1}.</span>
            <span>{html.escape(pt)}</span>
        </li>
    """ for i, pt in enumerate(reel.key_points)])

    snippets_html = "".join([f"""
        <div class="p-3.5 rounded-lg bg-slate-50 border border-slate-200 font-mono text-xs text-blue-700 mb-2 overflow-x-auto">
            {f'<span class="text-[10px] text-slate-400 block mb-1"># {html.escape(s.language_or_shell)}</span>' if s.language_or_shell else ''}
            <code>{html.escape(s.code)}</code>
        </div>
    """ for s in reel.code_snippets])

    is_post = reel.path_type in ("post", "p", "tv")
    is_not_analyzed = reel.status == "saved (not analyzed)" or reel.analysis_mode == "not_analyzed"

    post_badge_html = '<span class="text-xs font-bold px-2 py-0.5 rounded bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">📌 Post</span>' if is_post else ''

    mode_badge_html = ''
    if is_not_analyzed:
        mode_badge_html = '<span class="text-xs font-medium px-2 py-0.5 rounded bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300 border border-slate-200">Not analyzed</span>'
    elif reel.analysis_mode == "mock":
        mode_badge_html = '<span class="text-xs font-bold px-2 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200">🧪 Mock Data</span>'
    elif reel.analysis_mode == "video":
        mode_badge_html = '<span class="text-xs font-semibold px-2 py-0.5 rounded-full bg-purple-50 text-purple-700 border border-purple-200">🎬 Video</span>'
    elif reel.analysis_mode == "images":
        mode_badge_html = '<span class="text-xs font-semibold px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">🖼️ Images</span>'
    elif reel.analysis_mode == "caption_only":
        mode_badge_html = '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">📝 Caption</span>'
    else:
        mode_badge_html = '<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-sky-50 text-sky-700 border border-sky-200">🎙️ Audio</span>'

    watch_text = "Open post on Instagram" if is_post else "Watch original on Instagram"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(reel.title or reel.shortcode)} — Reel Vault</title>
    <!-- PWA Meta Tags -->
    <link rel="manifest" href="/manifest.json">
    <link rel="icon" type="image/svg+xml" href="/static/icons/icon.svg">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
        }};
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {{
            document.documentElement.classList.add('dark');
        }}
    </script>
</head>
<body class="min-h-screen py-8 px-4 sm:px-8 bg-slate-50 dark:bg-slate-950 text-slate-800 dark:text-slate-100">
    <div class="max-w-3xl mx-auto">
        <a href="/" class="inline-flex items-center gap-2 text-xs text-slate-500 hover:text-blue-600 font-semibold mb-6 transition">
            &larr; Back to Reels Knowledge Base
        </a>

        <div class="bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-xl p-6 sm:p-8 shadow-sm">
            <div class="flex items-center gap-2 mb-3 flex-wrap">
                <span class="text-xs font-semibold px-2 py-1 bg-blue-100 text-blue-700 rounded-full">
                    {html.escape(reel.category or 'General')}
                </span>
                {post_badge_html}
                {mode_badge_html}
                {f'<span class="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">⚠️ Limited Extraction</span>' if reel.is_thin_extraction else ""}
                <span class="text-xs font-semibold px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">
                    Confidence: {int(reel.confidence * 100)}%
                </span>
                <span class="text-xs text-slate-500 font-mono">{reel.shortcode}</span>
            </div>

            <h1 class="text-2xl font-bold text-slate-900 dark:text-white mb-3">
                {html.escape(reel.title or ('Post ' + reel.shortcode if is_post else 'Reel ' + reel.shortcode))}
            </h1>

            <div class="flex items-center gap-3 mb-6">
                <a href="{html.escape(reel.original_url)}" target="_blank" rel="noopener noreferrer" 
                   class="text-xs text-blue-600 hover:underline">
                    {watch_text} &rarr;
                </a>
                <button onclick="navigator.clipboard.writeText('{html.escape(reel.original_url)}'); alert('Instagram URL copied!');" 
                        class="px-2 py-0.5 rounded bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-mono">
                    📋 Copy URL
                </button>
            </div>

            <!-- Core Takeaway Card -->
            <div class="p-5 rounded-xl bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 mb-6">
                <h2 class="text-xs font-bold uppercase text-slate-500 mb-1.5">Core Takeaway</h2>
                <p class="text-sm font-medium text-slate-800 dark:text-slate-100 leading-relaxed">
                    {html.escape(reel.takeaway or 'No takeaway available.')}
                </p>
            </div>

            {f'''
            <div class="mb-6">
                <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Key Points</h3>
                <ul class="space-y-1.5 bg-slate-50 dark:bg-slate-950 p-4 rounded-xl border border-slate-200">
                    {points_html}
                </ul>
            </div>''' if reel.key_points else ''}

            {f'''
            <div class="mb-6">
                <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Tools & Resources</h3>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {tools_html}
                </div>
            </div>''' if reel.tools else ''}

            {f'''
            <div class="mb-6">
                <h3 class="text-xs font-bold uppercase tracking-wider text-slate-400 mb-3">Extracted External Links (Unverified)</h3>
                <div class="space-y-2">
                    {extracted_urls_html}
                </div>
            </div>''' if reel.extracted_urls else ''}

            {f'''
            <div class="mb-6">
                <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Code Snippets & Commands</h3>
                {snippets_html}
            </div>''' if reel.code_snippets else ''}

            {f'''
            <div class="mb-6">
                <h3 class="text-xs font-bold uppercase text-slate-400 mb-2">Speech Transcript</h3>
                <div class="p-3.5 rounded-lg bg-slate-50 dark:bg-slate-950 border border-slate-200 text-xs text-slate-700 font-mono max-h-40 overflow-y-auto">
                    {html.escape(reel.transcript)}
                </div>
            </div>''' if reel.transcript else ''}

            <div class="pt-6 border-t border-slate-200 flex items-center justify-between flex-wrap gap-2">
                <span class="text-xs text-slate-500">Recorded: {reel.created_at[:10] if reel.created_at else 'N/A'}</span>
                <div class="flex items-center gap-2">
                    <button onclick="if(confirm('Are you sure you want to remove this Reel from your vault?')) {{ fetch('/api/vault/reels/{reel.shortcode}', {{ method: 'DELETE' }}).then(r => {{ if (r.ok) window.location.href='/'; else alert('Failed to delete.'); }}); }}" 
                            class="px-4 py-2 rounded-lg border border-rose-200 text-rose-600 hover:bg-rose-50 text-xs font-semibold">
                        Delete Reel
                    </button>
                    <button onclick="if(confirm('Re-analyze this reel using multimodal video extraction?')) {{ this.disabled = true; this.textContent = '⏳ Processing...'; fetch('/api/vault/reels/{reel.shortcode}/reanalyze?force_video=true', {{ method: 'POST' }}).then(r => r.json()).then(d => {{ alert(d.message || 'Re-analysis started!'); setTimeout(() => window.location.reload(), 1500); }}).catch(e => alert('Error: ' + e)); }}"
                            class="px-4 py-2 rounded-lg bg-purple-50 text-purple-700 border border-purple-200 font-semibold text-xs">
                        🎬 Re-analyze (Video)
                    </button>
                    {f'''
                    <form method="POST" action="/api/vault/reels/{reel.shortcode}/retry">
                        <button type="submit" class="px-4 py-2 rounded-lg bg-rose-600 text-white font-bold text-xs">
                            Retry Analysis
                        </button>
                    </form>''' if reel.status in ('failed', 'unavailable') else ''}
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""
