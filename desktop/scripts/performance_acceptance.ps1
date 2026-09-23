param(
  [string]$Exe = 'C:\Users\Admin\Desktop\tonghop-main\tonghop-main\desktop\.update-test\install-acceptance-20260924\th-media-desktop.exe',
  [string]$BaseLocalAppData = 'C:\Users\Admin\Desktop\tonghop-main\tonghop-main\desktop\.perf-test\LocalAppData',
  [int]$Runs = 3,
  [int]$TimeoutSeconds = 30
)
$ErrorActionPreference='Stop'

function Desc([int]$parent){
  $all=@(Get-CimInstance Win32_Process)
  $result=New-Object System.Collections.Generic.List[int]
  $queue=New-Object System.Collections.Generic.Queue[int]
  $queue.Enqueue($parent)
  while($queue.Count -gt 0){
    $cur=$queue.Dequeue()
    foreach($c in $all|Where-Object{[int]$_.ParentProcessId -eq $cur}){
      $id=[int]$c.ProcessId
      if(-not $result.Contains($id)){$result.Add($id);$queue.Enqueue($id)}
    }
  }
  @($result)
}

$oldLocal=$env:LOCALAPPDATA
$oldToken=$env:TH_MEDIA_AUTH_TOKEN
New-Item -ItemType Directory -Force -Path $BaseLocalAppData | Out-Null
$results=@()

try{
  for($i=1;$i -le $Runs;$i++){
    $env:LOCALAPPDATA=$BaseLocalAppData
    $env:TH_MEDIA_AUTH_TOKEN='perf_'+[guid]::NewGuid().ToString('N')
    $main=$null
    try{
      $started=Get-Date
      $main=Start-Process -FilePath $Exe -WorkingDirectory (Split-Path $Exe) -PassThru
      $deadline=(Get-Date).AddSeconds($TimeoutSeconds)
      $backend=$null;$port=0
      while((Get-Date)-lt $deadline){
        $main.Refresh()
        if($main.HasExited){throw "PERF_MAIN_EXITED_$($main.ExitCode)"}
        $ids=@(Desc $main.Id)
        $children=@(Get-CimInstance Win32_Process|Where-Object{$ids -contains [int]$_.ProcessId})
        $backend=$children|Where-Object{$_.Name -eq 'th-media-backend.exe'}|Select-Object -First 1
        if($backend){
          $listen=Get-NetTCPConnection -State Listen -OwningProcess ([int]$backend.ProcessId) -ErrorAction SilentlyContinue|Select-Object -First 1
          if($listen){$port=[int]$listen.LocalPort;break}
        }
        Start-Sleep -Milliseconds 120
      }
      if($port -le 0){throw 'PERF_BACKEND_NOT_READY'}
      $readyMs=[math]::Round(((Get-Date)-$started).TotalMilliseconds)
      Start-Sleep -Seconds 3

      $ids=@(Desc $main.Id)
      $children=@(Get-CimInstance Win32_Process|Where-Object{$ids -contains [int]$_.ProcessId})
      $flowChildren=@($children|Where-Object{$_.Name -eq 'th-media-flow-bridge.exe'})
      $chromeChildren=@($children|Where-Object{$_.Name -eq 'chrome.exe'})
      $procIds=@($main.Id)+@($ids)
      $memory=0
      foreach($pidValue in $procIds){
        try{$memory += (Get-Process -Id $pidValue -ErrorAction Stop).WorkingSet64}catch{}
      }
      $db=Join-Path $BaseLocalAppData 'TH Media\Desktop\Database\aihub.db'

      $results += [pscustomobject]@{
        run=$i
        backend_ready_ms=$readyMs
        idle_working_set_mb=[math]::Round($memory/1MB,1)
        flow_child_count=$flowChildren.Count
        chrome_child_count=$chromeChildren.Count
        db_exists=(Test-Path -LiteralPath $db -PathType Leaf)
      }

      $p=Get-Process -Id $main.Id -ErrorAction Stop
      [void]$p.CloseMainWindow()
      $stop=(Get-Date).AddSeconds(12)
      while((Get-Date)-lt $stop){
        Start-Sleep -Milliseconds 200
        $p.Refresh()
        if($p.HasExited){break}
      }
      if(-not $p.HasExited){throw 'PERF_GRACEFUL_CLOSE_TIMEOUT'}
    } finally {
      if($main){
        try{
          $main.Refresh()
          if(-not $main.HasExited){
            $ids=@(Desc $main.Id)
            foreach($pidValue in ($ids|Sort-Object -Descending)){Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue}
            Stop-Process -Id $main.Id -Force -ErrorAction SilentlyContinue
          }
        }catch{}
      }
    }
  }

  $maxReady=($results|Measure-Object backend_ready_ms -Maximum).Maximum
  $avgReady=[math]::Round(($results|Measure-Object backend_ready_ms -Average).Average)
  $maxRam=($results|Measure-Object idle_working_set_mb -Maximum).Maximum
  $result=[ordered]@{
    ok=($maxReady -le 7000 -and $maxRam -le 500 -and (@($results|Where-Object{$_.flow_child_count -ne 0 -or $_.chrome_child_count -ne 0}).Count -eq 0) -and (@($results|Where-Object{-not $_.db_exists}).Count -eq 0))
    runs=$results
    average_backend_ready_ms=$avgReady
    maximum_backend_ready_ms=$maxReady
    maximum_idle_working_set_mb=$maxRam
    flow_lazy_pass=(@($results|Where-Object{$_.flow_child_count -ne 0}).Count -eq 0)
    chrome_lazy_pass=(@($results|Where-Object{$_.chrome_child_count -ne 0}).Count -eq 0)
    db_pass=(@($results|Where-Object{-not $_.db_exists}).Count -eq 0)
  }
  $result|ConvertTo-Json -Depth 8
  if(-not $result.ok){exit 1}
} finally {
  if($null -eq $oldLocal){Remove-Item Env:LOCALAPPDATA -ErrorAction SilentlyContinue}else{$env:LOCALAPPDATA=$oldLocal}
  if($null -eq $oldToken){Remove-Item Env:TH_MEDIA_AUTH_TOKEN -ErrorAction SilentlyContinue}else{$env:TH_MEDIA_AUTH_TOKEN=$oldToken}
}
