"""Run Peach's unittest suite by a documented product scope."""
from __future__ import annotations

import argparse
import fnmatch
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Iterable
from contextlib import nullcontext
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

if not __package__:
    sys.path.insert(0, str(ROOT))
    __package__ = "scripts"
from . import change_impact, test_evidence, test_progress

COMMON_PATTERNS = ("test_context_budget.py", "test_test_collection.py", "test_complexity_ratchet.py",
                   "test_source_assertion_ratchet.py", "test_python_lint.py")
SCOPES: dict[str, tuple[str, ...]] = {
    "checks": ("test_copy_final_state.py", "test_dependency_policy.py", "test_repo_hygiene.py", "test_test_planning.py",
               "test_complexity_ratchet.py", "test_source_assertion_ratchet.py", "test_python_lint.py"),
    "follow": ("test_follow*.py", "test_http.py", "test_migrations.py", "test_wants_web.py"),
    "catalog": ("test_ad_judgement.py", "test_composite_name_split.py", "test_media_libraries.py", "test_media_folders.py",
                "test_content_region.py",
                "test_duplicate_identity_merge.py",
                "test_entity_classification.py", "test_company_profiles.py", "test_fc2_sellers.py",
                "test_entity_merge.py", "test_entity_redirect.py", "test_fastapi_api.py", "test_field_owners.py",
                "test_migrations.py",
                "test_review_mirror.py", "test_rm_web.py",
                "test_entity_link_install.py", "test_web_links.py", "test_entry_links.py",
                "test_feeds.py", "test_feeds_web.py", "test_wants_web.py",
                "test_link_marks.py", "test_site_icons.py", "test_site_logos.py",
                "test_avatar_face.py",
                "test_brand_marks.py", "test_studio_icon_variants.py",
                "test_push_discovery.py", "test_media_probe.py",
                "test_downloads*.py", "test_web_downloads.py",
                "test_review_csv.py", "test_related.py",
                "test_search_suggest.py", "test_subtitles.py",
                "test_task_runs.py", "test_followups.py", "test_task_center_integration.py",
                "test_studio_followup.py", "test_creator_identity_followup.py", "test_sample_images.py",
                "test_jav_code_domain.py",
                "test_taste_history.py", "test_taste_preferences.py", "test_web_ui.py", "test_web_js.py",
                "test_web_perf.py", "test_web_resource_sync.py", "test_record_rehome.py", "test_wants.py",
                "test_magnet_chain.py",
                "test_web_orphan_records.py",
                "test_web_review.py", "test_web_settings.py"),
    # 任务中心的两个文件跟着 `test_jobs.py` 走：`jobs.py` 与 `task_runs.py` 是同一条
    # 接线的两端，改哪一端都要两边一起验。
    "media": ("test_external_playback.py", "test_local_player.py", "test_runtime_consistency.py", "test_endcard.py", "test_fastapi_api.py", "test_jobs.py",
              "test_task_runs.py", "test_followups.py", "test_task_center_integration.py",
              "test_frame_capture.py", "test_timeline_sheets.py", "test_processing_control.py",
              "test_web_timeline_thumbnails.py",
              "test_interaction.py", "test_media.py", "test_mp4repair.py",
              "test_mp4recover.py", "test_web_media_repair.py",
              "test_previews.py", "test_preview_transport.py",
              "test_providers.py", "test_segments.py", "test_streaming.py",
              "test_subtitles.py", "test_transcodes.py", "test_hardware_transcode.py", "test_strm.py",
              "test_guangya_sources.py", "test_media_sources.py"),
    "sync": ("test_sync*.py", "test_platform.py", "test_mount.py", "test_tray*.py", "test_native*.py", "test_macos_standalone.py", "test_log_retention.py",
             "test_mdns.py", "test_netwatch.py", "test_certs.py",
             "test_review_mirror.py", "test_runtime_prepare.py"),
    "metadata": ("test_custom_metadata.py", "test_scraping_access.py", "test_browser_transport.py", "test_metadata*.py", "test_genre_taxonomy.py", "test_processing_control.py",
                 "test_fc2*.py", "test_local_poster.py",
                 "test_community_catalog.py",
                 "test_babepedia_match.py",
                 "test_western_artwork.py",
                 "test_western_profiles.py",
                 "test_jav*.py", "test_code_creators.py", "test_tag_renames.py",
                 "test_reproject_snapshot_tags.py",
                 "test_stale_candidates.py", "test_logo_provider.py",
                 "test_avatar_provider.py", "test_avatar_face.py",
                 "test_face_detect.py", "test_face_match.py", "test_performer*.py",
                 "test_avatar_watermark.py", "test_avatar_picker.py",
                 "test_portrait_gaps.py", "test_portrait_artwork.py",
                 "test_r18_machine_translation_repair.py",
                 "test_social_avatar_harvest.py",
                 "test_series_localization.py",
                 "test_duplicate_identity_merge.py", "test_entity_merge.py", "test_entity_redirect.py",
                 "test_migrations.py",
                 "test_entity_link_install.py", "test_studio_site_harvest.py",
                 "test_studio_followup.py", "test_creator_identity_followup.py", "test_sample_images.py",
                 "test_company_profiles.py",
                 "test_performer_link_harvest.py", "test_directory_link_harvest.py",
                 "test_minnano_av.py", "test_agency_roster_harvest.py",
                 "test_performer_agency_resync.py",
                 "test_studio_name_localization.py",
                 "test_studio_name_localization_apply.py", "test_studio_icon_variants.py",
                 "test_maker_directory_harvest.py", "test_studio_name_variant_merge.py",
                 "test_javdb_cn_names.py",
                 "test_link_rediscovery.py", "test_link_label_owner.py",
                 "test_link_repair.py",
                 "test_resource_identification.py",
                 "test_agency_entity.py", "test_agency_reject.py", "test_label_maker.py",
                 "test_seed_pack.py", "test_seed_followup.py", "test_seed_corrections.py"),
    "tooling": ("test_scripts.py", "test_trash_junk.py", "test_auth.py", "test_access.py", "test_cli.py", "test_server.py", "test_diagnostics.py", "test_error_reporting.py", "test_remote_reporting.py", "test_library_sharing.py", "test_library_diagnostics.py", "test_script_policy.py",
                "test_scan.py", "test_record_rehome.py", "test_wants.py", "test_push_discovery.py", "test_media_probe.py", "test_subtitles.py", "test_onboarding.py", "test_configuration_sources.py", "test_folder_picker.py", "test_ledger_backups.py", "test_clear_camera_filename_codes.py",
                "test_agent_worktree.py", "test_test_evidence.py", "test_test_environment.py", "test_dependency_policy.py",
                "test_version_bump.py", "test_changelog.py", "test_release_due.py",
                "test_restart_windows_tray.py", "test_deploy_windows_tray.py", "test_runtime_prepare.py",
                "test_buildinfo.py", "test_versioning.py", "test_macos_standalone.py",
                "test_windows_update.py", "test_release_updates.py", "test_automatic_updates.py", "test_standalone_update.py", "test_certs.py", "test_config.py",
                "test_fsutil.py", "test_desktop_settings.py", "test_desktop_installer.py",
                "test_job_status.py", "test_jobs.py", "test_task_runs.py", "test_followups.py", "test_update_tasks.py",
                "test_task_center_integration.py", "test_reference_updates.py",
                "test_repo_hygiene.py", "test_seed_pack.py", "test_seed_followup.py",
                "test_review_csv.py", "test_jav_code_domain.py", "test_organize.py",
                "test_link_repair.py",
                "test_subprocess_encoding.py", "test_module_layering.py",
                "test_copy_final_state.py", "test_demo_dataset.py",
                "test_cloudflared_packaging.py", "test_tunnel.py", "test_process_job.py",
                "test_ffmpeg_build.py", "test_ffmpeg_bundle.py", "test_untrunc_build.py",
                "test_media_tool_import.py", "test_seed_corrections.py"),
    # 前端 island 层（ADR-0022）。产物与源码的断言不需要 Node；vitest 那部分在没有
    # npm 时自己跳过，所以这个域在任何机器上都能跑，`full` 也就自动包含它。
    # `test_web_perf.py` 两个域都登记：压缩与 ETag 是 API 交付（catalog），
    # 播放器按需加载的断言读 `web/app.js`（web），改任一侧都该被本域拦住。
    # `test_copy_final_state.py` 两个域都登记：它扫全树，而界面字串是它最常拦到的
    # 一面，改 `web/` 的人必须在本域就撞上它。
    # 后面七个文件的主体不在这一层，但各有一段断言读 `web/` 或 `frontend/` 的源码，
    # 所以本域也要登记它们：改了 `web/app.js` 却漏跑读它的测试，`test_test_planning.py`
    # 的域映射门槛会在本地就红。
    "web": ("test_frontend_build.py", "test_web_ui.py", "test_web_js.py", "test_web_e2e.py",
            "test_subtitle_search.py",
            "test_legacy_shell_routes.py",
            "test_web_perf.py", "test_copy_final_state.py",
            "test_agency_entity.py", "test_label_maker.py", "test_dependency_policy.py",
            "test_desktop_settings.py", "test_dev_copy.py",
            "test_fastapi_api.py", "test_follow_assets.py", "test_follow_web.py",
            "test_metadata_library.py", "test_processing_control.py", "test_studio_icon_variants.py", "test_web_settings.py", "test_web_library_shortcuts.py", "test_company_profiles.py"),
    "core": ("test_access.py", "test_auth.py", "test_config.py", "test_field_owners.py",
             "test_entity_classification.py",
             "test_migrations.py", "test_ledger_revision.py",
             "test_platform.py", "test_mount.py", "test_tray.py", "test_certs.py",
             "test_folder_picker.py", "test_fsutil.py", "test_runtime_consistency.py",
             "test_subprocess_encoding.py", "test_windows_update.py", "test_buildinfo.py"),
    "packaging": ("test_dependency_policy.py", "test_buildinfo.py", "test_onboarding.py", "test_macos_standalone.py",
                  "test_cli.py", "test_server.py", "test_versioning.py", "test_frontend_build.py",
                  "test_cloudflared_packaging.py", "test_desktop_installer.py", "test_native*.py",
                  "test_ffmpeg_build.py", "test_ffmpeg_bundle.py", "test_untrunc_build.py"),
}

SCOPE_TEST_IDS: dict[str, tuple[str, ...]] = {
    "follow": (
        "test_rm_web.WebDataTests.test_contract_handler_registries_are_complete_and_unknown_routes_fail",
        "test_rm_web.WebDataTests.test_read_only_post_routes_are_declared_and_all_exist",
        "test_scripts.OperationalScriptTests.test_test_entrypoints_guard_the_worktree_source_and_are_the_only_documented_command",
    ),
}

# `auto` 域按改动文件选域：每个文件先查 `AUTO_SCOPE_FILES` 的整路径，再按
# `AUTO_SCOPE_PREFIXES` 取第一个命中的前缀，多个文件取并集。表里没有的
# `tests/test_*.py` 直接按文件名归域，`src/peach/` 下的其余模块按「模块名 ↔ 测试文件名」
# 推断（`media.py` → `test_media.py` → media）。Markdown 归 checks。
#
# 一个文件可以落到多个域：读它的测试登记在哪个域，改它就得跑到哪个域。
# `tests/test_test_planning.py` 按测试源码里真实读到的路径反查这两张表，漏一条本地就红。
AUTO_SCOPE_FILES: dict[str, tuple[str, ...]] = {
    "scripts/fetch_ffmpeg_sources.py": ("tooling", "packaging"),
    "scripts/ffmpeg-sources.json": ("tooling", "packaging"),
    "scripts/build_ffmpeg_windows.sh": ("tooling", "packaging"),
    "scripts/fetch_untrunc_sources.py": ("tooling", "packaging"),
    "scripts/untrunc-sources.json": ("tooling", "packaging"),
    "scripts/build_untrunc_windows.sh": ("tooling", "packaging"),
    "scripts/media_tool_bundle.py": ("tooling", "packaging"),
    "src/peach/seed_corrections.py": ("metadata", "tooling"),
    "src/peach/seed_validation.py": ("metadata", "tooling"),
    "src/peach/preview_cache.py": ("follow", "catalog", "media", "web"),
    "src/peach/preview_transport.py": ("catalog", "media", "web"),
    "src/peach/dev_copy.py": ("web",),
    "scripts/dev/copy-editor.js": ("web",),
    # 路由页面同时被目录、工具与前端三个域的测试读源码。
    "src/peach/routes_pages.py": ("catalog", "tooling", "web"),
    # 托盘既是 sync 域的服务编排，也被版本与桌面设置那些 tooling 测试读源码。
    "src/peach/tray.py": ("sync", "tooling"),
    "src/peach/menubar.py": ("sync", "tooling"),
    # 托盘子服务与隧道的 cloudflared 共用这份 Job Object；两边的测试都要跑到。
    "src/peach/process_job.py": ("tooling", "sync"),
    # 托盘重启的实现由 `test_restart_windows_tray.py` 验，模块名推不出来。
    "src/peach/windows_restart.py": ("tooling",),
    # 托盘「重启服务」按路径拉起这个脚本（`--source --force`），托盘测试也要跑到。
    "scripts/restart_windows_tray.py": ("tooling", "sync"),
    "src/peach/jav_poster_crop.py": ("metadata", "web"),
    # genre 词表按名字只推得出 metadata。复核队列与 API 那两处的用例拿「词表没收的词」
    # 当素材：词表一收那个词，`test_web_review.py`（catalog）与 `test_fastapi_api.py`
    # （web）就红，而改词表的人跑不到那两个域——2026-09-21 就是这样把 master 跑红的。
    "src/peach/genre_taxonomy.py": ("metadata", "catalog", "web"),
    # 标签词表是 genre 投影的落点，复核页与目录页也按它筛选。
    "src/peach/catalog_rules.py": ("metadata", "catalog", "web"),
    # 来源顺序一变，`test_scripts.py` 里钉住的 field_rank 就跟着挪，而那份用例
    # 住在 tooling——按名字只推得出 metadata，不指明就跑不到。
    "src/peach/metadata_policy.py": ("metadata", "tooling"),
    # 处理任务的主体测试是 `test_metadata_library.py`，它登记在 metadata 与 web 两个域；
    # `test_stale_candidates.py` 在 metadata，`test_web_e2e.py` 在 web 里整条跑它。
    # 模块名与测试文件名对不上，按名字推不出来，不指明就退化成 full。
    "src/peach/library_processing.py": ("metadata", "web"),
    "src/peach/local_poster.py": ("metadata", "web"),
    "src/peach/processing_control.py": ("media", "metadata", "web"),
    # 刮削后继：主体测试 `test_followups.py` 与 `test_studio_followup.py`，按名字推不出来。
    "src/peach/avatar_followup.py": ("catalog", "media", "tooling"),
    "src/peach/avatar_offsite_cover_face.py": ("catalog", "media", "tooling"),
    "src/peach/studio_followup.py": ("catalog", "metadata"),
    "src/peach/creator_identity_followup.py": ("catalog", "metadata"),
    # 补样张后继的主体测试是 `test_sample_images.py`，出图路由与番号集在 `test_fastapi_api.py`。
    "src/peach/sample_followup.py": ("catalog", "metadata", "media"),
    "src/peach/sample_images.py": ("catalog", "metadata", "media"),
    # 厂牌判据由命令行与后继共用，两边的测试分住 catalog、metadata 与 web。
    "src/peach/studio_icons.py": ("catalog", "metadata", "web"),
    "src/peach/studio_sites.py": ("catalog", "metadata"),
    "src/peach/company_profiles.py": ("catalog", "metadata", "web"),
    "src/peach/fc2_sellers.py": ("catalog", "metadata", "web"),
    "scripts/import_fc2_sellers.py": ("catalog", "metadata", "tooling"),
    "scripts/harvest_company_profiles.py": ("catalog", "metadata", "web", "tooling"),
    # 失效标记由资料页、网页链接体检、维护脚本与种子导出共用。
    "src/peach/link_status.py": ("catalog", "metadata", "tooling", "web"),
    "scripts/revert_auto_landing.py": ("catalog", "metadata", "tooling"),
    # 实体事实种子包（ADR-0073）：导出导入的逻辑与命令行，测试都在 `test_seed_pack.py`；
    # 扫描结算后自动导入的后继（ADR-0075）在 `test_seed_followup.py`。
    "src/peach/seed_pack.py": ("metadata", "tooling"),
    "scripts/seed_pack.py": ("metadata", "tooling"),
    "src/peach/seed_followup.py": ("metadata", "tooling"),
    # 推广附件命名词库：垃圾复核队列（`test_ad_judgement.py`）与回收站脚本（`test_trash_junk.py`）都读它。
    "resources/junk/promo_attachment_names.json": ("catalog", "tooling"),
    # 补女优资料后继的解析与读写层、一次补完的脚本，测试都在 `test_performer_profile_followup.py`。
    "scripts/run_performer_profiles.py": ("metadata",),
    "src/peach/avwikidb.py": ("metadata",),
    "src/peach/performer_profiles.py": ("metadata",),
    # 女优页头的视图：主体测试在 `test_performer_header.py`（metadata），资料接口与骨架
    # 形状的用例在 catalog 与 web 两个域里经 `q_entity` 读它。
    "src/peach/performer_header.py": ("metadata", "catalog", "web"),
    # 推送发现横跨扫描登记（tooling）与 HTTP 端点（catalog），按名字只推得出一个域。
    "src/peach/push_discovery.py": ("tooling", "catalog"),
    # 个人记录的判据与搬运（ADR-0087）：资源同步与列表在 catalog，扫描登记与撤回脚本在 tooling。
    "src/peach/personal_records.py": ("catalog", "tooling"),
    "src/peach/record_rehome.py": ("catalog", "tooling"),
    "src/peach/web_orphan_records.py": ("catalog", "web"),
    "src/peach/web_library_shortcuts.py": ("catalog", "web"),
    # 入口页共用件的测试住在首启与配置来源那两份 tooling 测试里。
    "src/peach/web_entry.py": ("catalog", "tooling", "web"),
    # 这几份文档有测试在读它们的正文：改文档也要跑到那条测试。
    "README.md": ("checks", "tooling"),
    "README.en.md": ("checks", "tooling"),
    # 差异表是 token 判据的另一半：偏离上游的每一条都要在那张表里写明原因，所以
    # `test_frontend_build.py` 读它——改表就得跑到 web。
    "frontend/src/react/boardui/ORIGIN.md": ("checks", "web"),
    ".github/dependabot.yml": ("tooling", "web"),
    "Dockerfile": ("packaging", "tooling"),
    ".dockerignore": ("packaging", "tooling"),
    "compose.yaml": ("packaging", "tooling"),
    "src/peach/server.py": ("packaging", "tooling"),
    "scripts/smoke_docker.py": ("packaging", "tooling"),
}

# 脚本默认归 tooling；有自己领域测试的脚本两边都跑：领域测试验它的判据，
# tooling 里的脚本策略门槛验它的形态。
AUTO_SCOPE_PREFIXES: tuple[tuple[str, str | tuple[str, ...]], ...] = (
    ("src/peach/media.py", "media"),
    ("src/peach/local_player.py", "media"),
    ("src/peach/hardware_transcode.py", "media"),
    # 关键帧与 ctts 都从 moov 读，测试住在 `test_segments.py` 里。
    ("src/peach/mp4index.py", "media"),
    ("src/peach/mp4repair.py", "media"),
    ("src/peach/mp4recover.py", "media"),
    # 批量修复是播放链路的任务，但住在 web 层，两个域都要跑到。
    ("src/peach/web_media_repair.py", ("media", "web")),
    ("src/peach/desktop_startup.py", "tooling"),
    ("src/peach/desktop_uninstall.py", "tooling"),
    ("src/peach/peach_proxy.py", "tooling"),
    ("src/peach/gfriends.py", "metadata"),
    # 站点解析器契约与套了契约的站（ADR-0044）：测试住在 `test_metadata_sources.py` 与
    # `test_community_catalog.py`，都在 metadata 域；按包内模块名推不出来。
    ("src/peach/sources/", "metadata"),
    ("scripts/localize_performer_names.py", ("metadata", "tooling")),
    ("scripts/localize_series_names.py", ("metadata", "tooling")),
    ("scripts/localize_studio_names.py", ("metadata", "tooling")),
    ("scripts/apply_studio_name_localization.py", ("metadata", "tooling")),
    ("scripts/match_babepedia_creators.py", ("metadata", "tooling")),
    ("scripts/audit_code_creators.py", ("metadata", "tooling")),
    ("scripts/rename_retired_tags.py", ("metadata", "tooling")),
    ("scripts/reproject_snapshot_tags.py", ("metadata", "tooling")),
    ("scripts/drop_stale_genre_candidates.py", ("metadata", "tooling")),
    ("scripts/audit_fc2_similarity.py", ("metadata", "tooling")),
    ("scripts/poster_crop_boxes.py", ("metadata", "tooling")),
    ("scripts/rediscover_entity_links.py", ("metadata", "tooling")),
    ("scripts/repair_entity_links.py", ("metadata", "tooling")),
    ("scripts/fetch_studio_avatar_candidates.py", ("metadata", "tooling")),
    ("scripts/scrape_codes.py", ("metadata", "tooling")),
    ("scripts/repair_r18_machine_translations.py", ("metadata", "tooling")),
    ("scripts/merge_studio_name_variants.py", ("metadata", "tooling")),
    ("scripts/harvest_", ("metadata", "tooling")),
    # amane 桥（ADR-0043）：脚本由 metadata 域的单测装载，清单与锁由 checks 域的依赖策略核。
    ("tools/amane-bridge/", ("metadata", "checks")),
    ("scripts/sync_brand_marks.py", ("catalog", "tooling")),
    ("scripts/detect_cover_faces.py", ("web", "tooling")),
    ("scripts/demo_dataset.py", ("web", "tooling")),
    ("scripts/vendor_web_dependencies.mjs", ("web", "tooling")),
    ("scripts/audit_video_endcards.py", ("media", "tooling")),
    ("scripts/setup_macos_port80.sh", ("sync", "tooling")),
    # 订阅源的解析与建壳验在 `test_feeds*.py`，两份都登记在 catalog：壳的边界是
    # 「它不是 asset」，看得住这条的是目录域那批测试。
    ("src/peach/feeds.py", "catalog"),
    ("src/peach/feed_followup.py", "catalog"),
    # 云下载的任务模型、两个客户端与生成的 protobuf 代码，测试都是 `test_downloads*.py`。
    ("src/peach/downloads", "catalog"),
    ("src/peach/follow", "follow"),
    ("src/peach/fanbox.py", "follow"),
    ("src/peach/web_follow.py", "follow"),
    ("src/peach/sync", "sync"),
    ("src/peach/platform.py", "sync"),
    ("src/peach/mount.py", "sync"),
    ("src/peach/mdns.py", "sync"),
    ("src/peach/netwatch.py", "sync"),
    ("src/peach/certs.py", "sync"),
    # 产地的判据住在 `regions.py`，验它的测试叫 `test_content_region.py`：
    # 模块名与测试文件名对不上，按名字推不出来，只能在这里指明。
    ("src/peach/regions.py", "catalog"),
    ("src/peach/web_", "catalog"),
    ("src/peach/routes_", "catalog"),
    ("web/", "web"),
    ("frontend/", "web"),
    ("scripts/", "tooling"),
    ("pyproject.toml", "tooling"),
    (".github/", "tooling"),
    ("docs/", "tooling"),
    (".claude/", "tooling"),
)

# AGENTS.md 规定必须跑 `full` 的面：迁移、共享测试设施、依赖清单。命中任一个就不再选域。
FULL_ONLY_PREFIXES: tuple[str, ...] = (
    "pyproject.toml",
    "scripts/test_runner.py", "scripts/test_evidence.py", "scripts/test.ps1", "scripts/test.sh",
    "scripts/ci_plan.py", "scripts/test_environment.py", "scripts/change_impact.py", "scripts/test_progress.py", "uv.lock",
    "migrations/",
    "tests/support/",
    "package.json",
    "package-lock.json",
    "frontend/package.json",
    "frontend/package-lock.json",
)


def selected_files(scope: str) -> tuple[Path, ...]:
    if scope == "full":
        return tuple(sorted(TESTS.glob("test_*.py")))
    found: set[Path] = set()
    for pattern in (*COMMON_PATTERNS, *SCOPES[scope]):
        found.update(TESTS.glob(pattern))
    return tuple(sorted(found))


def scopes_of_test_file(name: str) -> tuple[str, ...]:
    """一个 `tests/test_*.py` 文件名登记在哪些域里；公共门槛文件归 tooling。"""
    scopes = tuple(scope for scope, patterns in SCOPES.items() if scope not in {"core", "packaging"}
                   if any(fnmatch.fnmatch(name, pattern) for pattern in patterns))
    if not scopes and any(fnmatch.fnmatch(name, pattern) for pattern in COMMON_PATTERNS):
        return ("tooling",)
    return scopes


def scopes_of_module(stem: str) -> tuple[str, ...]:
    """`src/peach/<stem>.py` 按测试文件名推断域：`test_<stem>.py` 或 `test_<stem>_*.py`。"""
    exact, prefix = f"test_{stem}.py", f"test_{stem}_"
    return tuple(scope for scope, patterns in SCOPES.items() if scope not in {"core", "packaging"}
                 if any(fnmatch.fnmatch(exact, pattern) or pattern.startswith(prefix)
                        for pattern in patterns))


#: `<某个 Path> / "a" / "b/c.js"` 这种拼接。要求左边有个 `/`，是为了把「路径当字符串
#: 参数传进去」的写法排除掉：`scopes_for_changes(["web/app.js"])` 喂的是假清单，
#: 不读真文件，不该因为字面量长得像路径就被算成读了它。
_PATH_CHAIN = re.compile(r"""/\s*((?:["'][^"'\n]+["']\s*/\s*)*["'][^"'\n]+["'])""")
_SEGMENT = re.compile(r"""["']([^"'\n]+)["']""")
#: 测试文件顶上的 `FRONTEND = ROOT / "frontend"` 这类别名，用来还原下面链式拼接的前缀。
_ROOT_ALIAS = re.compile(r"""^\w+\s*=\s*\w+(?:\[\d+\])?\s*/\s*["']([\w./-]+)["']\s*$""", re.M)


def repository_paths_read_by(source: str) -> tuple[str, ...]:
    """一段测试源码实际读到的仓库文件，仓库相对路径。

    只认拼在某个 Path 后面、且在仓库里真的存在的那一档。判据是「文件存在」而不是
    「长得像路径」：不存在的字面量是别的东西，存在的才是这个测试真正依赖的输入。
    """
    prefixes = {"", *(alias.strip("/") for alias in _ROOT_ALIAS.findall(source))}
    found: set[str] = set()
    for chain in _PATH_CHAIN.findall(source):
        joined = "/".join(segment.strip("/") for segment in _SEGMENT.findall(chain))
        for prefix in prefixes:
            candidate = f"{prefix}/{joined}".strip("/")
            if (ROOT / candidate).is_file():
                found.add(candidate)
    return tuple(sorted(found))


def dependency_inputs(source: str) -> dict:
    """提取依赖输入，供调用方比较构建与运行环境。"""
    return change_impact.dependency_inputs(source)


def changed_contents(root: Path, base: str, paths: Iterable[str]) -> dict:
    result = {}
    for path in paths:
        if not (path.endswith(".py") or path in {*change_impact.PACKAGE_FILES, "pyproject.toml"}):
            continue
        try:
            raw = test_evidence.git(root, "diff", "--raw", base, "--", path)
            modes = [part.lstrip(":") for part in raw.split()[:2]]
            if "120000" in modes or (len(modes) == 2 and "000000" not in modes and modes[0] != modes[1]):
                result[path] = (None, None)
                continue
            result[path] = (test_evidence.git(root, "show", f"{base}:{path}"),
                            (root / path).read_text(encoding="utf-8"))
        except (OSError, subprocess.CalledProcessError):
            pass
    return result


def special_scopes(path: str, kind: str) -> tuple[str, ...] | None:
    """内容类型、共享设施和工作流职责的优先边界。"""
    if kind == "unsafe":
        return ("full",)
    if kind == "prose":
        return ("checks",)
    if kind == "metadata":
        return ("packaging",)
    if kind == "version":
        return ("tooling",)
    if path == "scripts/copy-rules.json":
        return ("checks", "web")
    if change_impact.documentation(path):
        return AUTO_SCOPE_FILES.get(path, ("checks",))
    if path.startswith(("scripts/build_", "scripts/release_")) \
            or path in {".github/workflows/release.yml", ".github/workflows/upgrade-smoke.yml"}:
        return ("packaging", "tooling")
    if path.startswith(".github/workflows/"):
        return ("tooling",) if path in {".github/workflows/project-board.yml", ".github/workflows/docker.yml"} else ("full",)
    if path.rsplit("/", 1)[-1] == "conftest.py" or any(path.startswith(p) for p in FULL_ONLY_PREFIXES):
        return ("full",)
    return None


def mapped_scopes(path: str) -> tuple[str, ...]:
    """按显式映射、测试归属和模块测试名定位功能域。"""
    name = path.rsplit("/", 1)[-1]
    if path in AUTO_SCOPE_FILES:
        return AUTO_SCOPE_FILES[path]
    if path.startswith("tests/test_") and path.endswith(".py"):
        return scopes_of_test_file(name)
    for prefix, scope in AUTO_SCOPE_PREFIXES:
        if path.startswith(prefix):
            return (scope,) if isinstance(scope, str) else scope
    if path.startswith("src/peach/") and path.endswith(".py"):
        return scopes_of_module(name.removesuffix(".py"))
    return ()


def scopes_for_changes(paths: Iterable[str], *, contents: dict | None = None,
                       kinds: dict | None = None) -> tuple[tuple[str, ...], str]:
    """纯函数：改动清单与可选内容差异 → 验证范围和原因。"""
    picked: dict[str, list[str]] = {}
    full_reasons = []
    for raw in paths:
        path = raw.replace("\\", "/").strip("/")
        if not path:
            continue
        kind = (kinds or {}).get(path, "logic")
        if contents and path in contents:
            kind = change_impact.kind(path, *contents[path])
        scopes = special_scopes(path, kind)
        if scopes is None:
            scopes = mapped_scopes(path)
        if not scopes:
            full_reasons.append(f"{path} 映射不到任何域")
        elif "full" in scopes:
            full_reasons.append(f"{path} 属于必须 full 的面")
        else:
            for scope in scopes:
                picked.setdefault(scope, []).append(path)
    if full_reasons:
        return ("full",), "Peach auto scope: full <- " + "; ".join(full_reasons)
    if not picked:
        return ("checks",), "Peach auto scope: checks <- 没有改动文件，检查公共门槛"
    ordered = tuple(scope for scope in SCOPES if scope in picked)
    detail = "; ".join(f"{scope}: {', '.join(picked[scope])}" for scope in ordered)
    return ordered, f"Peach auto scope: {', '.join(ordered)} <- {detail}"


def changed_files(root: Path = ROOT, base: str = "master") -> list[str]:
    """分支相对 `base` 的提交、工作区已改动的文件和未跟踪文件，三者并集。"""
    commands = (
        ("git", "diff", "--name-only", "-z", f"{base}...HEAD"),
        ("git", "diff", "--name-only", "-z", "HEAD"),
        ("git", "ls-files", "--others", "--exclude-standard", "-z"),
    )
    found: set[str] = set()
    for command in commands:
        command = ("git", "-c", f"safe.directory={root.as_posix()}", *command[1:])
        output = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                encoding="utf-8", check=True).stdout
        found.update(part for part in output.split("\0") if part)
    return sorted(found)


def resolve_auto_scope() -> tuple[tuple[str, ...], str]:
    try:
        paths = changed_files()
    except (OSError, subprocess.CalledProcessError) as error:
        return ("full",), f"Peach auto scope: full <- git 不可用（{error}）"
    return scopes_for_changes(paths, contents=changed_contents(ROOT, "master", paths))


CI_FRONTEND_TESTS = frozenset({
    "test_frontend_build.VitestTests.test_the_frontend_sources_typecheck",
    "test_frontend_build.VitestTests.test_frontend_sources_and_tests_pass_lint",
    "test_frontend_build.VitestTests.test_the_island_suite_passes",
})

CI_BROWSER_TESTS = frozenset({
    "test_web_e2e.WebE2ESmokeTests.test_every_route_holds_the_layout_and_runtime_invariants",
    "test_web_e2e.SetupE2ETests.test_the_first_run_page_writes_errors_back_and_finishes",
})


def ci_frontend_delegated() -> bool:
    """Test 工作流的 Python 与浏览器任务由 web-bundle 承担前端工具检查。"""
    return (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_WORKFLOW") == "Test"
            and os.environ.get("GITHUB_JOB") in {"python", "web-e2e"})


def without_ci_frontend_tests(suite: unittest.TestSuite) -> unittest.TestSuite:
    """按完整用例 ID 分配 CI 职责，保留同模块的产物与样式契约。"""
    selected = unittest.TestSuite()
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            selected.addTests(without_ci_frontend_tests(test))
        elif test.id() not in CI_FRONTEND_TESTS and not (
                os.environ.get("GITHUB_JOB") == "python" and test.id() in CI_BROWSER_TESTS):
            selected.addTest(test)
    return selected


def build_suite(*scopes: str, shard_index: int = 0, shard_count: int = 1) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    files = sorted({path for scope in scopes for path in selected_files(scope)})
    assigned = set(files[shard_index::shard_count])
    sys.path[:0] = [str(ROOT), str(TESTS)]
    try:
        for path in sorted(assigned):
            suite.addTests(loader.loadTestsFromModule(importlib.import_module(path.stem)))
        for scope in scopes:
            for test_id in SCOPE_TEST_IDS.get(scope, ()):
                # 补充用例以模块名稳定分配；每个 shard 只运行自己的一份。
                module = TESTS / (test_id.split(".", 1)[0] + ".py")
                if module not in files and sum(test_id.split(".", 1)[0].encode()) % shard_count == shard_index:
                    suite.addTests(loader.loadTestsFromName(test_id))
    finally:
        del sys.path[:2]
    return without_ci_frontend_tests(suite) if ci_frontend_delegated() else suite


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        self.started = time.monotonic()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append((round(time.monotonic() - self.started, 3), test.id()))
        super().stopTest(test)


DEFAULT_JOBS = 4


def resolve_jobs(value: str) -> int:
    """`--jobs` 的取值：正整数照收，`auto` 使用本机默认并发；显式值无机器级上限。"""
    if value == "auto":
        return max(1, min(DEFAULT_JOBS, (os.cpu_count() or 1) // 2))
    try:
        jobs = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("--jobs 只接受正整数或 auto") from None
    if jobs < 1:
        raise argparse.ArgumentTypeError("--jobs 至少为 1")
    return jobs


def shard_command(scopes: tuple[str, ...], index: int, count: int, timings: Path) -> list[str]:
    command = [sys.executable, str(ROOT / "scripts" / "test_runner.py"),
            *(item for scope in scopes for item in ("--scope", scope)),
            "--shard-index", str(index), "--shard-count", str(count),
            "--timings", str(timings)]
    return command










def run_shards(scopes: tuple[str, ...], *, jobs: int, shard_count: int,
               spawn=subprocess.Popen) -> tuple[bool, int, list]:
    """运行独立分片并汇总证明；并发取调用方的 jobs，取消时清理自己的子进程。"""
    folder = Path(tempfile.mkdtemp(prefix="peach-shards-"))
    pending = list(range(shard_count))
    running = {}
    passed, count, timings = True, 0, []
    try:
        while pending or running:
            while pending and len(running) < jobs:
                index = pending.pop(0)
                log = open(folder / f"{index}.log", "w+", encoding="utf-8", errors="replace")
                try:
                    process = spawn(shard_command(scopes, index, shard_count, folder / f"{index}.json"),
                                    stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
                except BaseException:
                    log.close()
                    raise
                running[index] = (process, log)
            finished = [index for index, (process, _) in running.items() if process.poll() is not None]
            if not finished:
                time.sleep(0.2)
                continue
            for index in finished:
                process, log = running.pop(index)
                log.flush()
                log.seek(0)
                sys.stdout.write(log.read())
                log.close()
                report_path = folder / f"{index}.json"
                report = json.loads(report_path.read_text(encoding="utf-8")) \
                    if report_path.is_file() else {"success": False, "count": 0, "timings": []}
                ok = process.returncode == 0 and bool(report.get("success"))
                passed = passed and ok
                count += int(report.get("count", 0))
                timings.extend(tuple(item) for item in report.get("timings", []))
                print(f"分片 {index + 1}/{shard_count} {'通过' if ok else '失败'}"
                      f"（{report.get('count', 0)} 个用例）", flush=True)
    finally:
        for process, log in running.values():
            if process.poll() is None:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                else:
                    process.terminate()
                process.wait(timeout=15)
            log.close()
        shutil.rmtree(folder, ignore_errors=True)
    return passed, count, timings


def tools_needed_by(scopes: tuple[str, ...]) -> frozenset[str]:
    """本次选中的测试文件真正会用到的外部工具。

    判据是「选中的源码里出现了这个工具名」，不是一张手写的域到工具的表：测试搬域、
    新测试引入新工具时，手写的表会静静过期，而过期的表比没有表更坏——它会拿一个
    本次根本不启动的工具去挡住一次只改文档的 `checks`。
    """
    names = tuple(name for name, _ in test_evidence.TOOL_PROBES)
    needed: set[str] = set()
    for path in {path for scope in scopes for path in selected_files(scope)}:
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        needed.update(name for name in names if name in source)
    return frozenset(needed)


def environment_preflight(scopes: tuple[str, ...], timings: Path | None = None,
                          *, shard_count: int = 1) -> None:
    """外部工具若被执行权限挡住，在创建测试分片前给出一条可操作的结论。

    只有本次真的会用到的工具才判失败。受限环境里 ffmpeg 起不来是事实，但它不该挡住
    一次只改文档的 `checks`：那一轮一个媒体用例都不加载，硬失败只是把人赶出正式入口。
    其余工具仍然报出来，免得后面的失败被当成别的原因。

    分片子进程直接返回：域由父进程定下，它在切片前查过同一套工具，每片各查一遍只会
    把同一条结论打印 N 遍。
    """
    if shard_count > 1:
        return
    blocked = test_evidence.unspawnable_tools()
    if not blocked:
        return
    if os.environ.get("PEACH_SKIP_PREFLIGHT") == "1":
        print(f"跳过外部工具预检（PEACH_SKIP_PREFLIGHT=1）：{'、'.join(blocked)}", flush=True)
        return
    needed = tools_needed_by(scopes)
    fatal = tuple(name for name in blocked if name in needed)
    spared = tuple(name for name in blocked if name not in needed)
    if spared:
        print(f"本次范围不需要这些启动受限的工具，只作提示：{'、'.join(spared)}", flush=True)
    if not fatal:
        return
    print(f"Peach 测试环境无法启动 PATH 中的工具：{'、'.join(fatal)}。"
          "Windows Codex 任务请让 scripts/test.ps1 通过受控提权在正常 PowerShell 权限下运行；"
          "其他环境请先修复这些工具的执行权限。不要改单测或跳过用例。"
          "确认本次无关时用 PEACH_SKIP_PREFLIGHT=1 跳过预检。", flush=True)
    if timings is not None:
        timings.write_text(json.dumps({"success": False, "count": 0, "timings": []}),
                           encoding="utf-8")
    raise SystemExit(3)


def run_local_suite(scopes: tuple[str, ...], jobs: int) -> tuple[bool, int, list]:
    """按调用方选择的并发运行，串行全量通过分片释放测试缓存。"""
    chosen = {path for scope in scopes for path in selected_files(scope)}
    if (jobs > 1 or "full" in scopes) and len(chosen) > 1:
        shard_count = min(len(chosen), 4 * jobs)
        print(f"本机分片：{shard_count} 片、同时 {min(jobs, shard_count)} 个子进程", flush=True)
        return run_shards(scopes, jobs=jobs, shard_count=shard_count)
    result = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult).run(build_suite(*scopes))
    return result.wasSuccessful() and result.testsRun > 0, result.testsRun, result.timings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    # `--scope` 可重复：本机并行的父进程把 `auto` 算出来的几个域原样交给每个分片子进程。
    parser.add_argument("--scope", action="append", dest="scopes",
                        choices=("full", "auto", *SCOPES))
    parser.add_argument("--fresh", action="store_true", help="实际重跑，不复用本机记录")
    parser.add_argument("--focus", action="append", default=[], help="定向调试指定 Python/浏览器用例，不签发记录")
    parser.add_argument("--lock-timeout", type=float, default=1800,
                        help="全量锁等待秒数；0 表示锁忙时立即返回")
    parser.add_argument("--base", default="master", help="CI 选测的已验证 Git 基线")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--timings", type=Path, default=None,
                        help="分片子进程把成败与逐用例耗时写到这个文件，供父进程汇总")
    parser.add_argument("--jobs", type=resolve_jobs, default=1,
                        help="本机同时跑几个分片子进程；auto 按核数定。默认串行，正式入口传 auto")
    parser.add_argument("--list-scopes", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.lock_timeout <= 1800:
        parser.error("锁等待时间必须在 0～1800 秒之间")
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("分片总数必须为正数，编号从 0 开始且小于总数")
    if args.list_scopes:
        print("\n".join(("full", "auto", *SCOPES)))
        return 0
    if args.focus:
        if args.shard_count != 1:
            parser.error("定向调试不能与分片一起使用")
        from . import test_focus
        return test_focus.run(sys.modules[__name__], args)
    if os.environ.get("PEACH_TEST_FOCUS") or os.environ.get("PEACH_E2E_FILES"):
        parser.error("定向调试环境不能签发正式记录；请清除 PEACH_TEST_FOCUS 与 PEACH_E2E_FILES")
    requested: tuple[str, ...] = tuple(dict.fromkeys(args.scopes or ("auto",)))
    if "auto" in requested and len(requested) > 1:
        parser.error("auto 不能与别的域同时指定")
    scopes = requested
    if requested == ("auto",):
        if args.base == "master":
            scopes, explanation = resolve_auto_scope()
        else:
            paths = changed_files(ROOT, args.base)
            scopes, explanation = scopes_for_changes(paths, contents=changed_contents(ROOT, args.base, paths))
        print(explanation, flush=True)
    environment_preflight(scopes, args.timings, shard_count=args.shard_count)
    files = {path for scope in scopes for path in selected_files(scope)}
    print(f"Peach test scope: {' '.join(scopes)} ({len(files)} files)", flush=True)
    if args.shard_count > 1:
        # CI 的每片独立 runner；局部分片绝不签发本机全量证明。
        result = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult).run(
            build_suite(*scopes, shard_index=args.shard_index, shard_count=args.shard_count))
        success = result.wasSuccessful() and result.testsRun > 0
        if args.timings is not None:
            args.timings.write_text(json.dumps({"success": success, "count": result.testsRun,
                                                "timings": result.timings}), encoding="utf-8")
        return 0 if success else 1
    if ci_frontend_delegated():
        print("CI 前端工具检查由 web-bundle 验证；本次不签发本机测试记录。", flush=True)
        success, count, _ = run_local_suite(scopes, args.jobs)
        return 0 if success and count > 0 else 1
    try:
        guard = test_evidence.held(test_evidence.evidence_dir(ROOT) / "full-suite.lock",
                                  wait_seconds=args.lock_timeout,
                                  scope=" ".join(scopes), root=str(ROOT)) \
            if "full" in scopes else nullcontext()
        with guard:
            return verified_run(args, requested, scopes, files)
    except test_evidence.Timeout as error:
        lock = Path(error.lock_file)
        what = "本仓库全量测试" if lock.name == "full-suite.lock" else "相同状态的验证"
        print(f"{what}正在运行（{test_evidence.describe_holder(lock)}），请等待该次结果。",
              flush=True)
        return 2


def scoped_baseline(record, scopes, needed):
    """有效域记录只能补齐调用方所需范围，完整基线保留原通过时间。"""
    if "full" in needed:
        return None
    if "full" in record["passed"]:
        return {"full": record["validated"]["full"]}, needed
    if "full" in scopes:
        return None
    retained = {scope: record["validated"][scope] for scope in scopes
                if scope in record["passed"] and scope not in needed}
    if not retained:
        return None
    return retained, tuple(scope for scope in scopes if scope not in retained)


def incremental_scopes(context, scopes, files, *, failed=()):
    """选择有效基线，只补测受差异影响或缺少记录的域。"""
    choices = []
    if "full" in failed:
        return None, scopes
    for record, delta, version_only in test_evidence.baselines(ROOT, context, partial=True):
        if record.get("cohort") and record.get("coverage"):
            coverage = test_progress.retained(record, context, scopes_for_changes, selected_files)
            # 已知失败必须实际补测，不能从其他记录恢复同一批文件。
            invalid = test_progress.names(selected_files, failed)
            coverage = {name: stamp for name, stamp in coverage.items() if name not in invalid}
            if coverage:
                needed = test_progress.pending(coverage, selected_files, SCOPES)
                weight = len(test_progress.names(selected_files, needed))
                if weight <= len(files):
                    choices.append((weight, {**record, "coverage": coverage, "validated": {}}, needed))
            continue
        _, kinds = change_impact.delta(record["manifest"], context["manifest"])
        needed, _ = scopes_for_changes(delta, kinds=kinds) if delta else ((), "")
        if version_only and "full" not in needed:
            needed = tuple(dict.fromkeys((*needed, "tooling")))
        needed = tuple(dict.fromkeys((*needed, *failed)))
        reusable = scoped_baseline(record, scopes, needed)
        if reusable is None:
            continue
        retained, needed = reusable
        weight = len({p for scope in needed for p in selected_files(scope)})
        if weight <= len(files):
            choices.append((weight, {**record, "validated": retained}, needed))
    if not choices:
        return None, scopes
    _, baseline, scopes = min(choices, key=lambda item: item[0])
    print(f"复用基线 {baseline['state'][:12]}（{' '.join(baseline['validated'])}）；"
          f"差异补测：{' '.join(scopes) or '无'}", flush=True)
    return baseline, scopes


def verified_run(args, requested, scopes, files) -> int:
    """在调用方持有的全量锁内取得快照、验证并签发记录。"""
    context = test_evidence.inputs(ROOT)
    state = context["state"]
    with test_evidence.run_lock(ROOT, state, scope=" ".join(scopes), root=str(ROOT)):
        if not args.fresh and "full" not in requested and test_evidence.covers(
                test_evidence.read(ROOT, state), scopes):
            print("复用本机测试记录：代码、依赖环境和范围匹配（24 小时内）。", flush=True)
            return 0
        previous = test_evidence.read(ROOT, state)
        baseline = None
        if requested == ("auto",) and not args.fresh and (not previous or previous.get("passed") or previous.get("coverage")):
            baseline, scopes = incremental_scopes(context, scopes, files, failed=previous.get("failed_scopes", ()))
        actual = len({path for scope in scopes for path in selected_files(scope)})
        print(f"实际运行：{' '.join(scopes) or '无需补测'}（{actual} 个测试文件）", flush=True)
        folder = test_evidence.evidence_dir(ROOT)
        (folder / f"{state}.json").unlink(missing_ok=True)
        started = time.monotonic()
        passed, count, timings = run_local_suite(scopes, args.jobs) \
            if scopes else (True, 0, [])
        stable = state == test_evidence.key(ROOT)
        success = passed and stable
        slowest = sorted(timings, reverse=True)[:20]
        cohort, coverage = test_progress.after_run(context, scopes, success, previous, baseline,
                                                   selected_files, time.time())
        if success and test_progress.completion(coverage, selected_files):
            previous = {**previous, "validated": {**previous.get("validated", {}), "full": min(coverage.values())}}
        test_evidence.write(ROOT, state, scopes, success=success, previous=previous,
                            context=context, baseline=baseline,
                            cohort=cohort, coverage=coverage,
                            elapsed=time.monotonic() - started, slowest=slowest, count=count)
        for seconds, name in slowest[:5]:
            print(f"慢测试 {seconds:.3f}s：{name}", flush=True)
        if not stable:
            retain_inflight(context, scopes, passed, previous, baseline, count)
            return 4
        return 0 if success else 1


def retain_inflight(context, scopes, passed, previous, baseline, count):
    """运行中改动只保留同环境、完整批次中未受影响的结果。"""
    current = test_evidence.inputs(ROOT)
    cohort, coverage = test_progress.after_run(context, scopes, passed, previous, baseline,
                                               selected_files, time.time())
    source = {**context, "cohort": cohort, "coverage": coverage}
    kept = test_progress.retained(source, current, scopes_for_changes, selected_files) if passed else {}
    if kept:
        test_evidence.write(ROOT, current["state"], (), success=False, previous={}, context=current,
                            cohort=cohort, coverage=kept, elapsed=0, slowest=[], count=count)
    print(f"验证期间代码或依赖环境改变；保留 {len(kept)} 个未受影响的文件结果。"
          "请运行 auto 补齐验证，本次不签发完整通过记录。", flush=True)


if __name__ == "__main__":
    # 故障注入的默认错误日志属于当前测试进程，不能写进真实数据目录。
    from peach import error_reporting
    with tempfile.TemporaryDirectory(prefix='peach-test-errors-') as error_logs:
        error_reporting._log_dir = Path(error_logs)
        raise SystemExit(main())
