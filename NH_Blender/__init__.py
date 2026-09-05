bl_info = {
    "name": "NH Plugin for Blender",
    "author": "Enisam",
    "version": (0, 6, 2, 7),
    "blender": (5, 1, 1),
    "location": "3D Viewport > N-panel > NH Plugin",
    "description": "All-in-one Blender toolkit for porting and preparing DayZ/Arma assets: fixes, textures, colliders, proxies, snap points, and P3D workflow helpers.",    
    "doc_url": "https://github.com/BigbyOn/nh-blender-addon",
    "tracker_url": "https://github.com/BigbyOn/nh-blender-addon/issues",
    "mclink": "https://github.com/BigbyOn/nh-blender-addon",
    "category": "Object",
}

import bpy
import bmesh
from bpy.app.handlers import persistent
from bpy.types import Operator, Panel, PropertyGroup, UIList, OperatorFileListElement, Menu
from bpy.props import PointerProperty, StringProperty, FloatProperty, IntProperty, BoolProperty, EnumProperty, CollectionProperty
from mathutils import Vector, Matrix
import math
import random
import os
import re
import shutil
import subprocess
import importlib
import importlib.util
import json
import sys
from contextlib import contextmanager
import uuid
import hashlib
import tempfile

# ------------------------------------------------------------------------
#  Global config storage
# ------------------------------------------------------------------------


# ------------------------------------------------------------------------
#  NH Blender add-on package: module aggregator.
#  Bl_info/registration live here; feature code is split into nh_*.py
#  modules by domain (scatter, snap, colliders, textures, planner, assets...).
# ------------------------------------------------------------------------

from .nh_assets import (CRAY_OT_AssetLibraryAddCustomByName, CRAY_OT_AssetLibraryAddNewNHObjects, CRAY_OT_AssetLibraryBuildFromFiles, CRAY_OT_AssetLibraryBuildFromFolder, CRAY_OT_AssetLibraryBuildNHObjects, CRAY_OT_AssetLibraryCleanSourceArtifacts, CRAY_OT_AssetLibraryClear, CRAY_OT_AssetLibraryClearCustom, CRAY_OT_AssetLibraryOpenNHBrowser, CRAY_OT_AssetLibraryRemoveCustomByName, CRAY_OT_AssetCutToScene, CRAY_OT_AssetSaveToLibrary, CRAY_OT_ConvertSelectedToProxies, CRAY_OT_DeleteSelectedComponentsKeepVertices, CRAY_OT_MergeByDistanceKeepMaterials, CRAY_OT_OpenFixListFile, CRAY_OT_SelectFixListComponentsOnActiveLOD, CRAY_PG_AssetLibrarySettings)
from .nh_collider import (CRAY_OT_BuildCollider, CRAY_OT_ColliderHotkeysInfo, CRAY_OT_CopySelectedFacesToRoadway, CRAY_OT_CopySelectedVertsToGeometry, CRAY_OT_EnsureColliderLOD, CRAY_OT_EnsureRoadwayLOD, CRAY_OT_GenerateFakeTerrainGeometry, CRAY_OT_HullLooseGeometryVerts, CRAY_OT_OpenFireGeometryRvmatFolder, CRAY_OT_OpenNHKeymapPreferences, CRAY_OT_OpenRoadwayMaterialFolder, CRAY_OT_ReportNgonMeshes, CRAY_OT_RestoreNHDefaultKeymaps, CRAY_OT_SelectColliderMaterialFaces, CRAY_OT_SelectCoplanarPlateIslands, CRAY_OT_SelectIsolatedVertices, CRAY_OT_SelectLooseVerticesOutsideMemory, CRAY_OT_SelectNgonFaces, CRAY_OT_SelectSplitPlanarNgons, CRAY_OT_SetColliderTargetFromActive, CRAY_OT_SetFakeTerrainTargetFromActive, CRAY_OT_TriangulateNgonFaces, CRAY_OT_WeldRoadwayVertices)
from .nh_collider_exp import (CRAY_OT_CreateCylinderGuideColliderExp, CRAY_OT_CreatePipeGuideColliderExp, CRAY_OT_DeleteLastColliderExp, CRAY_OT_EnsureColliderLODExp, CRAY_OT_GenerateBoxColliderExp, CRAY_OT_GenerateCapsuleColliderExp, CRAY_OT_GenerateConvexHullColliderExp, CRAY_OT_GenerateCylinderBoxesColliderExp, CRAY_OT_GeneratePipeBoxesColliderExp, CRAY_OT_GenerateSphereColliderExp, CRAY_OT_RebuildConvexHullColliderExp, CRAY_OT_ReconvexSelectedComponentsExp, CRAY_OT_RunCollisionToolSelfTestExp, CRAY_OT_SelectConnectedShellFromSelectionExp, CRAY_OT_ValidateCollisionExp)
from .nh_fixes import (CRAY_OT_ClearPlainAxisPivots, CRAY_OT_ClearPlainAxisPivotsKeepZ, CRAY_OT_CreatePlainAxisPivot, CRAY_OT_FixProxyTriangleMeshes, CRAY_OT_IE_ExportCollectionsBatch, CRAY_OT_RepairP3DSelections, CRAY_PT_ClutterProxiesPanel, CRAY_PT_SnapPointsPanel)
from .nh_model_split import (CRAY_OT_ModelSplitGridClearCutters, CRAY_OT_ModelSplitGridCreateCutters, CRAY_OT_ModelSplitGridSelectCutters, CRAY_OT_ModelSplitGridSplitSource, CRAY_OT_ModelSplitMergeAddSource, CRAY_OT_ModelSplitMergeClearSources, CRAY_OT_ModelSplitMergeRemoveSource, CRAY_OT_ModelSplitMergeSelectedCollections, CRAY_PG_AssetProxySettings, CRAY_UL_ModelSplitMergeSources)
from .nh_planner import (CRAY_MT_P3DDropMenu, CRAY_OT_IEFilePathTooltip, CRAY_OT_IE_AddByName, CRAY_OT_IE_AddFiles, CRAY_OT_IE_AddSelectedCollections, CRAY_OT_IE_ClearFiles, CRAY_OT_IE_ImportBatch, CRAY_OT_IE_RefreshFiles, CRAY_OT_IE_RemoveFile, CRAY_OT_ModelSplitTransferSelectedToTargetCategory, CRAY_OT_P3DDropAddToPlanner, CRAY_OT_P3DDropImportNow, CRAY_OT_P3DDropMenu, CRAY_PG_IEFileItem, CRAY_PG_IEPlannerSettings, CRAY_UL_IEFiles)
from .nh_scatter import (CRAY_OT_LoadConfig, CRAY_OT_MoveUIPanelLayoutItem, CRAY_OT_ResetUIPanelLayoutOrder, CRAY_OT_ScatterProxies, CRAY_PG_ColliderExpSettings, CRAY_PG_ColliderSettings, CRAY_PG_ModelSplitMergeSourceItem, CRAY_PG_ModelSplitSettings, CRAY_PG_Settings, CRAY_PG_SnapSettings, CRAY_PG_UIPanelSettings)
from .nh_snap import (CRAY_OT_CreateSnapPairFromModelEdge, CRAY_OT_EnsureMemoryLOD, CRAY_OT_SnapBatchProcess, CRAY_OT_SnapSetP3DVisualsOnly, CRAY_OT_SnapShowAllP3DCollections, _P3DValidationCaptureLogger, _MemoryLodManager, _SnapPointNamePattern, _SnapPointPairBuilder)
from .nh_textures import (CRAY_OT_AssetLibraryForceRebuildIconsTextures, CRAY_OT_AssetLibraryFullRebuildFromZero, CRAY_OT_AssetLibraryRebuildIconCache, CRAY_OT_CancelTextureExport, CRAY_OT_CleanTextureConverterTestOutputs, CRAY_OT_ExportMissingTexturesFromSources, CRAY_OT_FixMeshHierarchy, CRAY_OT_OpenExpectedTextureToolsFolder, CRAY_OT_OpenNHAssetCacheFolder, CRAY_OT_OpenTextureCacheLastReport, CRAY_OT_OpenTextureExportLastReport, CRAY_OT_OpenTexturePreviewCacheFolder, CRAY_OT_PrintTextureConverterDiagnostics, CRAY_OT_PrintTextureExportDiagnostics, CRAY_OT_ReplaceTexturesFromDB, CRAY_OT_TexDBBuildFromFolder, CRAY_OT_TexSourceRootAdd, CRAY_OT_TexSourceRootRemove, CRAY_OT_TextureCacheBuild, CRAY_OT_TextureCacheBuildNHLibraryUsed, CRAY_OT_TextureCacheRebuildNHLibraryUsed, CRAY_OT_UpdateObjectPreview, CRAY_PG_ObjMatImagesItem, CRAY_PG_TexDBItem, CRAY_PG_TexReplaceSettings, CRAY_PG_TexSourceRootItem, CRAY_UL_ObjPreview, CRAY_UL_TexDB)
from .nh_ui_panels import (CRAY_PT_AssetProxyPanel, CRAY_PT_CacheManagerPanel, CRAY_PT_ColliderExpPanel, CRAY_PT_ColliderPanel, CRAY_PT_FixesPanel, CRAY_PT_ImportExportPlannerPanel, CRAY_PT_MenuSettingsPanel, CRAY_PT_ModelSplitPanel, CRAY_PT_TextureReplacePanel, _ensure_p3d_panel_icon_patch_timer)
from .nh_geometry_audit_ops import (CRAY_OT_GeometryAuditCleanSafe, CRAY_OT_GeometryAuditScan, CRAY_OT_GeometryAuditSelect, CRAY_PG_GeometryAuditDetail, CRAY_PG_GeometryAuditLODResult, CRAY_PG_GeometryAuditSettings, _clear_geometry_audit_cache)

from .nh_assets import (CRAY_OT_AssetLibraryAddCustomByName, CRAY_OT_AssetLibraryAddNewNHObjects, CRAY_OT_AssetLibraryBuildFromFiles, CRAY_OT_AssetLibraryBuildFromFolder, CRAY_OT_AssetLibraryBuildNHObjects, CRAY_OT_AssetLibraryCleanSourceArtifacts, CRAY_OT_AssetLibraryClear, CRAY_OT_AssetLibraryClearCustom, CRAY_OT_AssetLibraryOpenNHBrowser, CRAY_OT_AssetLibraryRemoveCustomByName, CRAY_OT_AssetCutToScene, CRAY_OT_AssetSaveToLibrary, CRAY_OT_ConvertSelectedToProxies, CRAY_OT_DeleteSelectedComponentsKeepVertices, CRAY_OT_MergeByDistanceKeepMaterials, CRAY_OT_OpenFixListFile, CRAY_OT_SelectFixListComponentsOnActiveLOD, CRAY_PG_AssetLibrarySettings)
from .nh_base import (_PERSISTED_UI_STATE_TIMER_INTERVAL, _apply_persisted_ui_state_to_all_scenes, _collect_persisted_ui_state, _deferred_restore_persisted_ui_state, _persisted_ui_state_timer, _read_persisted_ui_state, _register_collider_keymaps, _restore_persisted_ui_state_on_load, _save_current_persisted_ui_state, _unregister_collider_keymaps)
from .nh_collider import (CRAY_OT_BuildCollider, CRAY_OT_ColliderHotkeysInfo, CRAY_OT_CopySelectedFacesToRoadway, CRAY_OT_CopySelectedVertsToGeometry, CRAY_OT_EnsureColliderLOD, CRAY_OT_EnsureRoadwayLOD, CRAY_OT_GenerateFakeTerrainGeometry, CRAY_OT_HullLooseGeometryVerts, CRAY_OT_OpenFireGeometryRvmatFolder, CRAY_OT_OpenNHKeymapPreferences, CRAY_OT_OpenRoadwayMaterialFolder, CRAY_OT_ReportNgonMeshes, CRAY_OT_RestoreNHDefaultKeymaps, CRAY_OT_SelectColliderMaterialFaces, CRAY_OT_SelectCoplanarPlateIslands, CRAY_OT_SelectIsolatedVertices, CRAY_OT_SelectLooseVerticesOutsideMemory, CRAY_OT_SelectNgonFaces, CRAY_OT_SelectSplitPlanarNgons, CRAY_OT_SetColliderTargetFromActive, CRAY_OT_SetFakeTerrainTargetFromActive, CRAY_OT_TriangulateNgonFaces, CRAY_OT_WeldRoadwayVertices)
from .nh_collider_exp import (CRAY_OT_CreateCylinderGuideColliderExp, CRAY_OT_CreatePipeGuideColliderExp, CRAY_OT_DeleteLastColliderExp, CRAY_OT_EnsureColliderLODExp, CRAY_OT_GenerateBoxColliderExp, CRAY_OT_GenerateCapsuleColliderExp, CRAY_OT_GenerateConvexHullColliderExp, CRAY_OT_GenerateCylinderBoxesColliderExp, CRAY_OT_GeneratePipeBoxesColliderExp, CRAY_OT_GenerateSphereColliderExp, CRAY_OT_RebuildConvexHullColliderExp, CRAY_OT_ReconvexSelectedComponentsExp, CRAY_OT_RunCollisionToolSelfTestExp, CRAY_OT_SelectConnectedShellFromSelectionExp, CRAY_OT_ValidateCollisionExp)
from .nh_fixes import (CRAY_OT_ClearPlainAxisPivots, CRAY_OT_ClearPlainAxisPivotsKeepZ, CRAY_OT_CreatePlainAxisPivot, CRAY_OT_FixProxyTriangleMeshes, CRAY_OT_IE_ExportCollectionsBatch, CRAY_OT_RepairP3DSelections, CRAY_PT_ClutterProxiesPanel, CRAY_PT_SnapPointsPanel)
from .nh_model_split import (CRAY_OT_ModelSplitGridClearCutters, CRAY_OT_ModelSplitGridCreateCutters, CRAY_OT_ModelSplitGridSelectCutters, CRAY_OT_ModelSplitGridSplitSource, CRAY_OT_ModelSplitMergeAddSource, CRAY_OT_ModelSplitMergeClearSources, CRAY_OT_ModelSplitMergeRemoveSource, CRAY_OT_ModelSplitMergeSelectedCollections, CRAY_PG_AssetProxySettings, CRAY_UL_ModelSplitMergeSources)
from .nh_planner import (CRAY_MT_P3DDropMenu, CRAY_OT_IEFilePathTooltip, CRAY_OT_IE_AddByName, CRAY_OT_IE_AddFiles, CRAY_OT_IE_AddSelectedCollections, CRAY_OT_IE_ClearFiles, CRAY_OT_IE_ImportBatch, CRAY_OT_IE_RefreshFiles, CRAY_OT_IE_RemoveFile, CRAY_OT_ModelSplitTransferSelectedToTargetCategory, CRAY_OT_P3DDropAddToPlanner, CRAY_OT_P3DDropImportNow, CRAY_OT_P3DDropMenu, CRAY_PG_IEFileItem, CRAY_PG_IEPlannerSettings, CRAY_UL_IEFiles, _ensure_p3d_import_patch_timer, _ensure_p3d_p3d_file_handler_patch_timer, _patch_p3d_import_read_file, _unpatch_p3d_import_read_file)
from .nh_scatter import (CRAY_OT_LoadConfig, CRAY_OT_MoveUIPanelLayoutItem, CRAY_OT_ResetUIPanelLayoutOrder, CRAY_OT_ScatterProxies, CRAY_PG_ColliderExpSettings, CRAY_PG_ColliderSettings, CRAY_PG_ModelSplitMergeSourceItem, CRAY_PG_ModelSplitSettings, CRAY_PG_Settings, CRAY_PG_SnapSettings, CRAY_PG_UIPanelSettings, _apply_ui_panel_class_order, _ui_panel_settings_from_context)
from .nh_snap import (CRAY_OT_CreateSnapPairFromModelEdge, CRAY_OT_EnsureMemoryLOD, CRAY_OT_SnapBatchProcess, CRAY_OT_SnapSetP3DVisualsOnly, CRAY_OT_SnapShowAllP3DCollections, _ensure_p3d_bundle_registered, _patch_p3d_p3d_file_handler, _unpatch_p3d_p3d_file_handler, _unregister_p3d_bundle)
from .nh_textures import (CRAY_OT_AssetLibraryForceRebuildIconsTextures, CRAY_OT_AssetLibraryFullRebuildFromZero, CRAY_OT_AssetLibraryRebuildIconCache, CRAY_OT_CancelTextureExport, CRAY_OT_CleanTextureConverterTestOutputs, CRAY_OT_ExportMissingTexturesFromSources, CRAY_OT_FixMeshHierarchy, CRAY_OT_OpenExpectedTextureToolsFolder, CRAY_OT_OpenNHAssetCacheFolder, CRAY_OT_OpenTextureCacheLastReport, CRAY_OT_OpenTextureExportLastReport, CRAY_OT_OpenTexturePreviewCacheFolder, CRAY_OT_PrintTextureConverterDiagnostics, CRAY_OT_PrintTextureExportDiagnostics, CRAY_OT_ReplaceTexturesFromDB, CRAY_OT_TexDBBuildFromFolder, CRAY_OT_TexSourceRootAdd, CRAY_OT_TexSourceRootRemove, CRAY_OT_TextureCacheBuild, CRAY_OT_TextureCacheBuildNHLibraryUsed, CRAY_OT_TextureCacheRebuildNHLibraryUsed, CRAY_OT_UpdateObjectPreview, CRAY_PG_ObjMatImagesItem, CRAY_PG_TexDBItem, CRAY_PG_TexReplaceSettings, CRAY_PG_TexSourceRootItem, CRAY_UL_ObjPreview, CRAY_UL_TexDB)
from .nh_ui_panels import (CRAY_PT_AssetProxyPanel, CRAY_PT_CacheManagerPanel, CRAY_PT_ColliderExpPanel, CRAY_PT_ColliderPanel, CRAY_PT_FixesPanel, CRAY_PT_ImportExportPlannerPanel, CRAY_PT_MenuSettingsPanel, CRAY_PT_ModelSplitPanel, CRAY_PT_TextureReplacePanel, _ensure_p3d_panel_icon_patch_timer)

# --- public/debug surface of the split package (ops candidates + bridge) ---
from .nh_snap import (_P3D_BUNDLE_REGISTRY, _P3D_IMPORT_CANDIDATES, _P3D_EXPORT_CANDIDATES,
    _call_first_available, _op_handle, _has_any_p3d_io_ops, _has_any_p3d_import_ops)

# --- public surface: DayZ config helpers ---
from .utilities.dayz_config import (parse_dayz_config, build_clutter_distribution,
    pick_weighted_random, CONFIG_SURFACES, CONFIG_CLUTTER)

from . import nh_statistics as _stats
from . import nh_ui_icons as _nh_icons

classes = (
    CRAY_PG_Settings,
    CRAY_PG_SnapSettings,
    CRAY_PG_ColliderSettings,
    CRAY_PG_ColliderExpSettings,
    CRAY_PG_UIPanelSettings,
    CRAY_PG_GeometryAuditDetail,
    CRAY_PG_GeometryAuditLODResult,
    CRAY_PG_GeometryAuditSettings,
    CRAY_OT_MoveUIPanelLayoutItem,
    CRAY_OT_ResetUIPanelLayoutOrder,
    CRAY_OT_LoadConfig,
    CRAY_OT_ScatterProxies,
    CRAY_OT_EnsureMemoryLOD,
    CRAY_OT_SnapSetP3DVisualsOnly,
    CRAY_OT_SnapShowAllP3DCollections,
    CRAY_OT_CreateSnapPairFromModelEdge,
    CRAY_OT_SnapBatchProcess,
    CRAY_OT_CopySelectedVertsToGeometry,
    CRAY_OT_HullLooseGeometryVerts,
    CRAY_OT_ColliderHotkeysInfo,
    CRAY_OT_OpenNHKeymapPreferences,
    CRAY_OT_RestoreNHDefaultKeymaps,
    CRAY_OT_SetColliderTargetFromActive,
    CRAY_OT_SetFakeTerrainTargetFromActive,
    CRAY_OT_EnsureRoadwayLOD,
    CRAY_OT_CopySelectedFacesToRoadway,
    CRAY_OT_WeldRoadwayVertices,
    CRAY_OT_OpenRoadwayMaterialFolder,
    CRAY_OT_OpenFireGeometryRvmatFolder,
    CRAY_OT_SelectColliderMaterialFaces,
    CRAY_OT_GenerateFakeTerrainGeometry,
    CRAY_OT_OpenFixListFile,
    CRAY_OT_SelectFixListComponentsOnActiveLOD,
    CRAY_OT_DeleteSelectedComponentsKeepVertices,
    CRAY_OT_MergeByDistanceKeepMaterials,
    CRAY_OT_FixProxyTriangleMeshes,
    CRAY_OT_GeometryAuditScan,
    CRAY_OT_GeometryAuditSelect,
    CRAY_OT_GeometryAuditCleanSafe,
    CRAY_OT_SelectIsolatedVertices,
    CRAY_OT_SelectLooseVerticesOutsideMemory,
    CRAY_OT_ReportNgonMeshes,
    CRAY_OT_SelectSplitPlanarNgons,
    CRAY_OT_SelectCoplanarPlateIslands,
    CRAY_OT_SelectNgonFaces,
    CRAY_OT_TriangulateNgonFaces,
    CRAY_OT_EnsureColliderLOD,
    CRAY_OT_BuildCollider,
    CRAY_OT_EnsureColliderLODExp,
    CRAY_OT_GenerateBoxColliderExp,
    CRAY_OT_GenerateConvexHullColliderExp,
    CRAY_OT_RebuildConvexHullColliderExp,
    CRAY_OT_ReconvexSelectedComponentsExp,
    CRAY_OT_DeleteLastColliderExp,
    CRAY_OT_SelectConnectedShellFromSelectionExp,
    CRAY_OT_CreateCylinderGuideColliderExp,
    CRAY_OT_CreatePipeGuideColliderExp,
    CRAY_OT_GenerateCylinderBoxesColliderExp,
    CRAY_OT_GeneratePipeBoxesColliderExp,
    CRAY_OT_GenerateSphereColliderExp,
    CRAY_OT_GenerateCapsuleColliderExp,
    CRAY_OT_ValidateCollisionExp,
    CRAY_OT_RunCollisionToolSelfTestExp,

    CRAY_PG_TexDBItem,
    CRAY_PG_ObjMatImagesItem,
    CRAY_PG_TexSourceRootItem,
    CRAY_PG_TexReplaceSettings,
    CRAY_UL_TexDB,
    CRAY_UL_ObjPreview,
    CRAY_OT_TexDBBuildFromFolder,
    CRAY_OT_TexSourceRootAdd,
    CRAY_OT_TexSourceRootRemove,
    CRAY_OT_UpdateObjectPreview,
    CRAY_OT_FixMeshHierarchy,
    CRAY_OT_ReplaceTexturesFromDB,
    CRAY_OT_PrintTextureExportDiagnostics,
    CRAY_OT_PrintTextureConverterDiagnostics,
    CRAY_OT_OpenTextureExportLastReport,
    CRAY_OT_OpenExpectedTextureToolsFolder,
    CRAY_OT_CleanTextureConverterTestOutputs,
    CRAY_OT_CancelTextureExport,
    CRAY_OT_ExportMissingTexturesFromSources,
    CRAY_OT_TextureCacheBuild,
    CRAY_OT_TextureCacheBuildNHLibraryUsed,
    CRAY_OT_OpenTexturePreviewCacheFolder,
    CRAY_OT_OpenTextureCacheLastReport,
    CRAY_OT_OpenNHAssetCacheFolder,
    CRAY_OT_AssetLibraryRebuildIconCache,
    CRAY_OT_AssetLibraryFullRebuildFromZero,
    CRAY_OT_AssetLibraryForceRebuildIconsTextures,
    CRAY_OT_TextureCacheRebuildNHLibraryUsed,

    CRAY_PG_IEFileItem,
    CRAY_PG_IEPlannerSettings,
    CRAY_PG_ModelSplitMergeSourceItem,
    CRAY_PG_ModelSplitSettings,
    CRAY_PG_AssetLibrarySettings,
    CRAY_PG_AssetProxySettings,
    CRAY_OT_IEFilePathTooltip,
    CRAY_UL_IEFiles,
    CRAY_OT_AssetLibraryBuildFromFolder,
    CRAY_OT_AssetLibraryBuildFromFiles,
    CRAY_OT_AssetLibraryClear,
    CRAY_OT_AssetLibraryCleanSourceArtifacts,
    CRAY_OT_AssetLibraryBuildNHObjects,
    CRAY_OT_AssetLibraryAddNewNHObjects,
    CRAY_OT_AssetLibraryOpenNHBrowser,
    CRAY_OT_AssetLibraryAddCustomByName,
    CRAY_OT_AssetLibraryRemoveCustomByName,
    CRAY_OT_AssetLibraryClearCustom,
    CRAY_OT_AssetCutToScene,
    CRAY_OT_AssetSaveToLibrary,
    CRAY_OT_IE_AddFiles,
    CRAY_OT_P3DDropMenu,
    CRAY_MT_P3DDropMenu,
    CRAY_OT_P3DDropAddToPlanner,
    CRAY_OT_P3DDropImportNow,
    CRAY_OT_IE_AddByName,
    CRAY_OT_IE_RemoveFile,
    CRAY_OT_IE_ClearFiles,
    CRAY_OT_IE_AddSelectedCollections,
    CRAY_OT_IE_RefreshFiles,
    CRAY_OT_IE_ImportBatch,
    CRAY_OT_ModelSplitTransferSelectedToTargetCategory,
    CRAY_OT_ModelSplitGridCreateCutters,
    CRAY_OT_ModelSplitGridSelectCutters,
    CRAY_OT_ModelSplitGridClearCutters,
    CRAY_OT_ModelSplitGridSplitSource,
    CRAY_UL_ModelSplitMergeSources,
    CRAY_OT_ModelSplitMergeAddSource,
    CRAY_OT_ModelSplitMergeRemoveSource,
    CRAY_OT_ModelSplitMergeClearSources,
    CRAY_OT_ModelSplitMergeSelectedCollections,
    CRAY_OT_ConvertSelectedToProxies,
    CRAY_OT_RepairP3DSelections,
    CRAY_OT_CreatePlainAxisPivot,
    CRAY_OT_ClearPlainAxisPivots,
    CRAY_OT_ClearPlainAxisPivotsKeepZ,
    CRAY_OT_IE_ExportCollectionsBatch,

    CRAY_PT_ColliderPanel,
    CRAY_PT_ColliderExpPanel,
    CRAY_PT_ClutterProxiesPanel,
    CRAY_PT_SnapPointsPanel,
    CRAY_PT_AssetProxyPanel,
    CRAY_PT_FixesPanel,
    CRAY_PT_ImportExportPlannerPanel,
    CRAY_PT_ModelSplitPanel,
    CRAY_PT_TextureReplacePanel,
    CRAY_PT_CacheManagerPanel,
    CRAY_PT_MenuSettingsPanel,
)

def register():
    global _PERSISTED_UI_STATE_CACHE

    _stats.wrap(classes)
    _registered_classes = []
    try:
        for cls in classes:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        _nh_icons.apply_to_panels(classes)
    except Exception:
        for cls in reversed(_registered_classes):
            try:
                bpy.utils.unregister_class(cls)
            except Exception:
                pass
        raise
    bpy.types.Scene.cray_settings = PointerProperty(type=CRAY_PG_Settings)
    bpy.types.Scene.cray_snap_settings = PointerProperty(type=CRAY_PG_SnapSettings)
    bpy.types.Scene.cray_collider_settings = PointerProperty(type=CRAY_PG_ColliderSettings)
    bpy.types.Scene.cray_collider_exp_settings = PointerProperty(type=CRAY_PG_ColliderExpSettings)
    bpy.types.Scene.cray_geometry_audit_settings = PointerProperty(type=CRAY_PG_GeometryAuditSettings)
    bpy.types.Scene.cray_texreplace_settings = PointerProperty(type=CRAY_PG_TexReplaceSettings)
    bpy.types.Scene.cray_ie_settings = PointerProperty(type=CRAY_PG_IEPlannerSettings)
    bpy.types.Scene.cray_model_split_settings = PointerProperty(type=CRAY_PG_ModelSplitSettings)
    bpy.types.Scene.cray_asset_library_settings = PointerProperty(type=CRAY_PG_AssetLibrarySettings)
    bpy.types.Scene.cray_asset_proxy_settings = PointerProperty(type=CRAY_PG_AssetProxySettings)
    bpy.types.Scene.cray_ui_panel_settings = PointerProperty(type=CRAY_PG_UIPanelSettings)
    _PERSISTED_UI_STATE_CACHE = _read_persisted_ui_state()
    _apply_persisted_ui_state_to_all_scenes(only_if_default=True)
    _apply_ui_panel_class_order(_ui_panel_settings_from_context(bpy.context))
    _PERSISTED_UI_STATE_CACHE = _collect_persisted_ui_state(getattr(bpy.context, "scene", None))
    if not bpy.app.timers.is_registered(_deferred_restore_persisted_ui_state):
        bpy.app.timers.register(_deferred_restore_persisted_ui_state, first_interval=0.2)
    if _restore_persisted_ui_state_on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_restore_persisted_ui_state_on_load)
    if not bpy.app.timers.is_registered(_persisted_ui_state_timer):
        bpy.app.timers.register(_persisted_ui_state_timer, first_interval=_PERSISTED_UI_STATE_TIMER_INTERVAL, persistent=True)
    _ensure_p3d_bundle_registered()
    _stats.start()
    _patch_p3d_import_read_file()
    if not bpy.app.timers.is_registered(_ensure_p3d_import_patch_timer):
        bpy.app.timers.register(_ensure_p3d_import_patch_timer, first_interval=1.0, persistent=True)
    _patch_p3d_p3d_file_handler()
    if not bpy.app.timers.is_registered(_ensure_p3d_p3d_file_handler_patch_timer):
        bpy.app.timers.register(_ensure_p3d_p3d_file_handler_patch_timer, first_interval=1.0, persistent=True)
    _register_collider_keymaps()
    if not bpy.app.timers.is_registered(_ensure_p3d_panel_icon_patch_timer):
        bpy.app.timers.register(_ensure_p3d_panel_icon_patch_timer, first_interval=1.0)

def unregister():
    _clear_geometry_audit_cache()
    _nh_icons.dispose()
    _stats.stop()
    _unregister_collider_keymaps()
    _unregister_p3d_bundle()
    _save_current_persisted_ui_state(getattr(bpy.context, "scene", None))
    if bpy.app.timers.is_registered(_deferred_restore_persisted_ui_state):
        bpy.app.timers.unregister(_deferred_restore_persisted_ui_state)
    if bpy.app.timers.is_registered(_persisted_ui_state_timer):
        bpy.app.timers.unregister(_persisted_ui_state_timer)
    if bpy.app.timers.is_registered(_ensure_p3d_panel_icon_patch_timer):
        bpy.app.timers.unregister(_ensure_p3d_panel_icon_patch_timer)
    if _restore_persisted_ui_state_on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_restore_persisted_ui_state_on_load)
    if bpy.app.timers.is_registered(_ensure_p3d_import_patch_timer):
        bpy.app.timers.unregister(_ensure_p3d_import_patch_timer)
    if bpy.app.timers.is_registered(_ensure_p3d_p3d_file_handler_patch_timer):
        bpy.app.timers.unregister(_ensure_p3d_p3d_file_handler_patch_timer)
    _unpatch_p3d_p3d_file_handler()
    _unpatch_p3d_import_read_file()
    for attr_name in (
        "cray_geometry_audit_settings",
        "cray_ui_panel_settings",
        "cray_asset_proxy_settings",
        "cray_asset_library_settings",
        "cray_model_split_settings",
        "cray_ie_settings",
        "cray_texreplace_settings",
        "cray_collider_exp_settings",
        "cray_collider_settings",
        "cray_snap_settings",
        "cray_settings",
    ):
        if hasattr(bpy.types.Scene, attr_name):
            delattr(bpy.types.Scene, attr_name)
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass

if __name__ == "__main__":
    register()
