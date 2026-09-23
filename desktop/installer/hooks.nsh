!macro NSIS_HOOK_PREINSTALL
  CreateDirectory "$LOCALAPPDATA\TH Media\Updates"
  IfFileExists "$LOCALAPPDATA\TH Media\Updates\rollback-in-progress" thmedia_update_mark_pending thmedia_update_rotate

  thmedia_update_rotate:
    IfFileExists "$LOCALAPPDATA\TH Media\Updates\rollback-current.exe" 0 thmedia_update_mark_pending
    Delete "$LOCALAPPDATA\TH Media\Updates\rollback-previous.exe"
    Rename "$LOCALAPPDATA\TH Media\Updates\rollback-current.exe" "$LOCALAPPDATA\TH Media\Updates\rollback-previous.exe"

  thmedia_update_mark_pending:
    FileOpen $0 "$LOCALAPPDATA\TH Media\Updates\pending-healthcheck" w
    FileWrite $0 "pending"
    FileClose $0
!macroend

!macro NSIS_HOOK_POSTINSTALL
  CreateDirectory "$LOCALAPPDATA\TH Media\Updates"
  CopyFiles /SILENT "$EXEPATH" "$LOCALAPPDATA\TH Media\Updates\rollback-current.exe"
  CreateDirectory "$LOCALAPPDATA\TH Media\Desktop\Backups\Installers"
  CopyFiles /SILENT "$EXEPATH" "$LOCALAPPDATA\TH Media\Desktop\Backups\Installers\TH Media_${VERSION}_x64-setup.exe"
  Delete "$LOCALAPPDATA\TH Media\Updates\rollback-in-progress"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Stop TH Media processes that may still hold files or the local database.
  nsExec::ExecToLog 'taskkill /F /IM th-media-backend.exe'
  nsExec::ExecToLog 'taskkill /F /IM th-media-flow-bridge.exe'
  nsExec::ExecToLog 'taskkill /F /IM th-media-desktop.exe'

  ; Preserve user projects/data by default. The first/default choice is Keep Data.
  MessageBox MB_YESNO|MB_ICONQUESTION "Bạn có muốn GIỮ LẠI toàn bộ dự án, database, media, Flow session, logs và cài đặt TH Media trên máy này không?$\r$\n$\r$\nChọn Yes để giữ dữ liệu. Chọn No nếu bạn muốn xem tùy chọn xóa toàn bộ." /SD IDYES IDYES thmedia_keep_data IDNO thmedia_confirm_delete

  thmedia_confirm_delete:
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "XÓA TOÀN BỘ sẽ xóa vĩnh viễn dự án, database, media, Flow session, logs và cài đặt TH Media trong $LOCALAPPDATA\TH Media.$\r$\n$\r$\nBạn có chắc chắn muốn xóa toàn bộ không?" /SD IDNO IDYES thmedia_delete_data IDNO thmedia_keep_data

  thmedia_delete_data:
    RMDir /r "$LOCALAPPDATA\TH Media"
    Goto thmedia_uninstall_choice_done

  thmedia_keep_data:
    DetailPrint "Giữ lại dữ liệu người dùng tại $LOCALAPPDATA\TH Media"

  thmedia_uninstall_choice_done:
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Best-effort second pass in case a sidecar was restarted while uninstalling.
  nsExec::ExecToLog 'taskkill /F /IM th-media-backend.exe'
  nsExec::ExecToLog 'taskkill /F /IM th-media-flow-bridge.exe'
!macroend
