import { invoke } from '@tauri-apps/api/core'
import { disable, enable, isEnabled } from '@tauri-apps/plugin-autostart'
import { getRuntimeConfig } from './runtime'

export type DesktopSettings = {
  media_dir: string
  legacy_media_dirs: string[]
  language: 'vi'
  backup_keep: number
  minimize_to_tray: boolean
  notifications_enabled: boolean
  chrome_path: string
  temp_quota_gb: number
  log_max_mb: number
  log_keep: number
  default_video_model: string
  default_video_aspect_ratio: string
  default_video_resolution: string
  paths: {
    data_root: string
    database_path: string
    flow_profile_dir: string
    flow_sessions_dir: string
    temp_dir: string
    logs_dir: string
    backups_dir: string
  }
}
export async function loadDesktopSettings(): Promise<DesktopSettings> {
  return invoke<DesktopSettings>('get_desktop_settings')
}

export async function saveDesktopSettings(
  settings: Partial<DesktopSettings>,
): Promise<DesktopSettings> {
  return invoke<DesktopSettings>('save_desktop_settings', { settings })
}

export async function setDesktopMediaDirectory(path: string): Promise<string> {
  return invoke<string>('set_media_directory', { path })
}

export async function openProjectCanonicalFolder(projectId: string): Promise<string> {
  return invoke<string>('open_project_canonical_folder', { projectId })
}

export async function ensureDesktopFlowRuntime(): Promise<void> {
  try {
    await invoke('ensure_flow_runtime')
  } catch (error) {
    if (getRuntimeConfig().backendBaseUrl) throw error
  }
}

export async function restartDesktopApp(): Promise<void> {
  await invoke('restart_th_media')
}

export async function getAutostartEnabled(): Promise<boolean> {
  return isEnabled()
}

export async function setAutostartEnabled(enabled: boolean): Promise<void> {
  if (enabled) await enable()
  else await disable()
}
