param(
  [string]$Exe = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\.update-test\install-acceptance-20260924\th-media-desktop.exe')),
  [string]$LocalAppData = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\.security-test\LocalAppData')),
  [int]$TimeoutSeconds = 30
)
$ErrorActionPreference='Stop'
function Desc([int]$parent){
  $all=@(Get-CimInstance Win32_Process)
  $result=New-Object System.Collections.Generic.List[int]
  $q=New-Object System.Collections.Generic.Queue[int];$q.Enqueue($parent)
  while($q.Count -gt 0){$cur=$q.Dequeue();foreach($c in $all|Where-Object{[int]$_.ParentProcessId -eq $cur}){$id=[int]$c.ProcessId;if(-not $result.Contains($id)){$result.Add($id);$q.Enqueue($id)}}}
  @($result)
}
$oldLocal=$env:LOCALAPPDATA;$oldToken=$env:TH_MEDIA_AUTH_TOKEN
$token='security_'+[guid]::NewGuid().ToString('N')
$env:LOCALAPPDATA=$LocalAppData;$env:TH_MEDIA_AUTH_TOKEN=$token
New-Item -ItemType Directory -Force -Path $LocalAppData|Out-Null
$main=$null
try{
  $main=Start-Process -FilePath $Exe -WorkingDirectory (Split-Path $Exe) -PassThru
  $deadline=(Get-Date).AddSeconds($TimeoutSeconds);$backend=$null;$port=0
  while((Get-Date)-lt $deadline){
    $main.Refresh();if($main.HasExited){throw "MAIN_EXITED_$($main.ExitCode)"}
    $ids=@(Desc $main.Id);$children=@(Get-CimInstance Win32_Process|Where-Object{$ids -contains [int]$_.ProcessId})
    $backend=$children|Where-Object{$_.Name -eq 'th-media-backend.exe'}|Select-Object -First 1
    if($backend){$listen=Get-NetTCPConnection -State Listen -OwningProcess ([int]$backend.ProcessId) -ErrorAction SilentlyContinue|Select-Object -First 1;if($listen){$port=[int]$listen.LocalPort;break}}
    Start-Sleep -Milliseconds 150
  }
  if($port -le 0){throw 'BACKEND_NOT_READY'}
  $ids=@(Desc $main.Id);$allPids=@([int]$main.Id)+@($ids)
  $listeners=@()
  foreach($pidValue in $allPids){$listeners += @(Get-NetTCPConnection -State Listen -OwningProcess $pidValue -ErrorAction SilentlyContinue)}
  $nonLoop=@($listeners|Where-Object{$_.LocalAddress -notin @('127.0.0.1','::1')})
  $missingStatus=0;$wrongStatus=0;$validStatus=0
  try{Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/desktop/network" -TimeoutSec 8|Out-Null;$missingStatus=200}catch{if($_.Exception.Response){$missingStatus=[int]$_.Exception.Response.StatusCode}else{$missingStatus=-1}}
  try{Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/desktop/network" -Headers @{'X-TH-Media-Token'='wrong'} -TimeoutSec 8|Out-Null;$wrongStatus=200}catch{if($_.Exception.Response){$wrongStatus=[int]$_.Exception.Response.StatusCode}else{$wrongStatus=-1}}
  try{Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/desktop/network" -Headers @{'X-TH-Media-Token'=$token} -TimeoutSec 8|Out-Null;$validStatus=200}catch{if($_.Exception.Response){$validStatus=[int]$_.Exception.Response.StatusCode}else{$validStatus=-1}}
  $result=[ordered]@{
    ok=($nonLoop.Count -eq 0 -and $missingStatus -eq 401 -and $wrongStatus -eq 401 -and $validStatus -eq 200)
    listener_count=$listeners.Count
    listener_addresses=@($listeners|ForEach-Object{"$($_.LocalAddress):$($_.LocalPort)"})
    non_loopback_count=$nonLoop.Count
    missing_token_status=$missingStatus
    wrong_token_status=$wrongStatus
    valid_token_status=$validStatus
  }
  $result|ConvertTo-Json -Depth 6
  if(-not $result.ok){exit 1}
}finally{
  if($main){try{$main.Refresh();if(-not $main.HasExited){$ids=@(Desc $main.Id);foreach($id in ($ids|Sort-Object -Descending)){Stop-Process -Id $id -Force -ErrorAction SilentlyContinue};Stop-Process -Id $main.Id -Force -ErrorAction SilentlyContinue}}catch{}}
  if($null -eq $oldLocal){Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue}else{$env:LOCALAPPDATA=$oldLocal}
  if($null -eq $oldToken){Remove-Item Env:TH_MEDIA_AUTH_TOKEN -ErrorAction SilentlyContinue}else{$env:TH_MEDIA_AUTH_TOKEN=$oldToken}
}
