import { getCurrentWindow } from '@tauri-apps/api/window'
import { isPermissionGranted, onAction, requestPermission, sendNotification } from '@tauri-apps/plugin-notification'
import { getRuntimeConfig } from './runtime'

let permissionPromise: Promise<boolean> | null = null

async function ensurePermission() {
  if (!getRuntimeConfig().backendBaseUrl) return false
  if (!permissionPromise) {
    permissionPromise = (async () => {
      try {
        if (await isPermissionGranted()) return true
        return (await requestPermission()) === 'granted'
      } catch {
        return false
      }
    })()
  }
  return permissionPromise
}

export async function notifyDesktop(title: string, body: string, extra?: Record<string, unknown>) {
  if (!(await ensurePermission())) return false
  try {
    sendNotification({ title, body, extra, autoCancel: true })
    return true
  } catch {
    return false
  }
}

export async function onDesktopNotificationAction(
  handler: (extra: Record<string, unknown>) => void | Promise<void>,
) {
  if (!getRuntimeConfig().backendBaseUrl) return null
  return onAction(async notification => {
    try {
      const window = getCurrentWindow()
      await window.show()
      await window.unminimize()
      await window.setFocus()
    } catch {
      // Continue routing even if the window was already visible.
    }
    await handler(notification.extra || {})
  })
}
