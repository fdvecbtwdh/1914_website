# 1914.fun 停止服务器
$ErrorActionPreference = "SilentlyContinue"
$procs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
    Where-Object { $_.CommandLine -like "*wsgi.py*" -or $_.CommandLine -like "*run.py*" }
if ($procs) {
    $procs | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force
        Write-Host "[ok] 已停止进程 $($_.ProcessId)" -ForegroundColor Green
    }
} else {
    Write-Host "[..] 服务器未在运行"
}
