<#
================================================================
 小智 AI 一键部署（PowerShell 版） — deploy.ps1
================================================================
 用法（PowerShell 里，项目根目录下执行）：

   powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -Ip YOUR_SERVER_IP -Domain chat.example.com

 或者先放开本次会话的执行策略，再执行：

   Set-ExecutionPolicy -Scope Process Bypass
   .\deploy\deploy.ps1 -Ip YOUR_SERVER_IP -Domain chat.example.com

 特点（不用装 Git Bash / rsync）：
   1. 用 Windows 自带的 ssh / scp / tar
   2. 上传前自动备份服务器代码（可回滚）
   3. Nginx 配置若被手动改过 → 只备份、不覆盖
      （兼容 chat-agent.conf 和 chat-agent 两种文件命名）
   4. 部署后自动健康检查
================================================================
#>

param(
    [Parameter(Mandatory = $true)][string]$Ip,
    [string]$Domain = "",
    [string]$User = "root",
    [switch]$AutoCreateUser
)

# 避免中文输出乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$ProgressPreference = "SilentlyContinue"

if ([string]::IsNullOrWhiteSpace($Domain)) { $Domain = $Ip }

$AppDir      = "/opt/chat-agent"
$NginxBase   = "/etc/nginx/sites-available/chat-agent"
$ServiceConf = "/etc/systemd/system/chat-agent.service"
$Ts          = Get-Date -Format "yyyyMMdd-HHmmss"
$Target      = "$User@$Ip"

# 项目根目录（脚本在 deploy/ 下，往上一级）
$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = (Get-Location).Path }

function Invoke-Remote {
    param([string]$Cmd, [switch]$AllowFail)
    ssh -o StrictHostKeyChecking=accept-new $Target $Cmd
    if (-not $AllowFail -and $LASTEXITCODE -ne 0) {
        throw "远程命令执行失败（退出码 $LASTEXITCODE）"
    }
}

Write-Host "========================================"
Write-Host " 小智 AI — 部署到 $Ip"
Write-Host " 域名: $Domain"
Write-Host " 时间: $Ts"
Write-Host "========================================"

# ----------------------------------------------------------------
# 0. 连通性检查
# ----------------------------------------------------------------
Write-Host "[0/6] 检查连接..."
Invoke-Remote "echo   已连接: `$(hostname)"

# ----------------------------------------------------------------
# 1. 备份服务器上的代码
# ----------------------------------------------------------------
Write-Host "[1/6] 备份服务器代码..."
Invoke-Remote "mkdir -p $AppDir/backups && tar czf $AppDir/backups/code-backup-$Ts.tgz -C $AppDir backend frontend requirements.txt 2>/dev/null; ls -1t $AppDir/backups | head -3" -AllowFail

# ----------------------------------------------------------------
# 2. 打包并上传代码（不用 rsync）
# ----------------------------------------------------------------
Write-Host "[2/6] 打包并上传代码..."
$tgz = Join-Path $env:TEMP "chat-agent-$Ts.tgz"
Push-Location $Root
& tar -czf $tgz --exclude=__pycache__ --exclude=*.pyc --exclude=.env backend frontend requirements.txt
$tarOk = $LASTEXITCODE
Pop-Location
if ($tarOk -ne 0) { throw "本地打包失败，请确认项目根目录有 backend / frontend / requirements.txt" }

& scp -q $tgz "${Target}:/tmp/chat-agent-$Ts.tgz"
if ($LASTEXITCODE -ne 0) { throw "上传失败（scp）" }

Invoke-Remote "mkdir -p $AppDir && tar xzf /tmp/chat-agent-$Ts.tgz -C $AppDir && mkdir -p $AppDir/frontend/plots && rm -f /tmp/chat-agent-$Ts.tgz && echo   上传完成"
Remove-Item $tgz -Force -ErrorAction SilentlyContinue

# ----------------------------------------------------------------
# 3. 安装依赖
# ----------------------------------------------------------------
Write-Host "[3/6] 安装/更新依赖..."
Invoke-Remote "$AppDir/venv/bin/pip install -q -r $AppDir/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple" -AllowFail
Write-Host "  依赖步骤完成（若失败且本次没有新增依赖，可继续）"

# ----------------------------------------------------------------
# 4. systemd 服务（内容没变就不动）
# ----------------------------------------------------------------
Write-Host "[4/6] 检查 systemd 服务配置..."
& scp -q "$Root\deploy\chat-agent.service" "${Target}:/tmp/chat-agent.service.new"
if ($LASTEXITCODE -ne 0) { throw "上传 systemd 配置失败" }

Invoke-Remote "if [ -f $ServiceConf ] && diff -q $ServiceConf /tmp/chat-agent.service.new >/dev/null; then echo   无变化,跳过; rm -f /tmp/chat-agent.service.new; else if [ -f $ServiceConf ]; then cp -f $ServiceConf $ServiceConf.bak-$Ts; echo   已备份旧配置到 $ServiceConf.bak-$Ts; fi; cp -f /tmp/chat-agent.service.new $ServiceConf && rm -f /tmp/chat-agent.service.new && systemctl daemon-reload && systemctl enable chat-agent >/dev/null 2>&1 && echo   已更新; fi"

# ---- 检查 unit 指定的运行用户是否存在（缺失会导致服务 217/USER 反复重启）----
# 默认【不自动创建】：只告警并中止，由使用者决定；要自动创建需显式加 -AutoCreateUser
$svcUser = (& ssh -o StrictHostKeyChecking=accept-new $Target "sed -n s/^User=//p $ServiceConf | head -1" | Out-String).Trim()
if ($svcUser -and $svcUser -ne "root") {
    $userExists = (& ssh -o StrictHostKeyChecking=accept-new $Target "getent passwd $svcUser >/dev/null && echo yes || echo no" | Out-String).Trim()
    if ($userExists -eq "yes") {
        Write-Host "  运行用户 $svcUser 已存在 OK"
    }
    elseif ($AutoCreateUser) {
        Write-Host "  运行用户 $svcUser 不存在 -> 按 -AutoCreateUser 创建受限系统账号"
        Invoke-Remote "useradd -r -s /bin/false $svcUser && echo    已创建（系统账号：不可登录、无 sudo、非管理员）"
    }
    else {
        Write-Host ""
        Write-Host "  已中止：unit 里指定的运行用户 '$svcUser' 在服务器上不存在，服务会启动失败。"
        Write-Host "  该账号是【受限系统账号】：不可登录、无 sudo、不是管理员，仅用于跑本服务。"
        Write-Host "  确认后手动创建："
        Write-Host "    ssh $Target 'useradd -r -s /bin/false $svcUser'"
        Write-Host "  或本次部署允许自动创建（在命令末尾加）：-AutoCreateUser"
        Write-Host ""
        throw "缺少运行用户，已中止（避免把服务重启成不可用状态）"
    }
    Invoke-Remote "chown -R $svcUser`:$svcUser $AppDir/data $AppDir/tmp-recycle 2>/dev/null; echo    运行时目录已授权"
}

# ----------------------------------------------------------------
# 5. Nginx 配置（安全模式）
# ----------------------------------------------------------------
Write-Host "[5/6] 检查 Nginx 配置（安全模式）..."

# 兼容两种命名：chat-agent.conf / chat-agent
$confSuffix = & ssh -o StrictHostKeyChecking=accept-new $Target "if [ -f $NginxBase.conf ]; then echo .conf; elif [ -f $NginxBase ]; then echo none; else echo .conf; fi"
$NginxConf = if ("$confSuffix".Trim() -eq "none") { $NginxBase } else { "$NginxBase.conf" }
Write-Host "  目标配置文件: $NginxConf"

& scp -q "$Root\deploy\chat-agent.conf" "${Target}:/tmp/chat-agent.conf.new"
if ($LASTEXITCODE -ne 0) { throw "上传 nginx 模板失败" }
Invoke-Remote "sed -i s/your-domain.com/$Domain/g /tmp/chat-agent.conf.new"

$nginxScript = @"
CONF=$NginxConf
NEW=/tmp/chat-agent.conf.new
if [ -f `$CONF ]; then
  if diff -q `$CONF `$NEW >/dev/null; then
    rm -f `$NEW
    echo   服务器配置与模板一致，无需改动
  else
    cp -f `$CONF `${CONF}.bak-$Ts
    echo   ⚠ 检测到自定义改动，已备份到 `${CONF}.bak-$Ts，本次不覆盖
    echo   新模板保留在服务器 /tmp/chat-agent.conf.new
  fi
else
  cp -f `$NEW `$CONF && rm -f `$NEW
  ln -sf `$CONF /etc/nginx/sites-enabled/`$(basename `$CONF)
  echo   首次安装完成
fi
if nginx -t >/dev/null 2>&1; then systemctl reload nginx; echo   nginx -t 通过，已重载; else echo   nginx -t 失败（站点仍用旧配置，未重载）; exit 1; fi
"@
Invoke-Remote $nginxScript

# ----------------------------------------------------------------
# 6. 重启服务 + 健康检查
# ----------------------------------------------------------------
Write-Host "[6/6] 重启服务并做健康检查..."
Invoke-Remote "systemctl restart chat-agent; sleep 5; echo -n '  服务状态: '; systemctl is-active chat-agent; H=`$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/health); if [ `"`$H`" = `"200`" ]; then echo '  /api/health:               200 OK'; echo -n '  /js/vendor/echarts.min.js: '; curl -s -o /dev/null -w '%{http_code} %{size_download} 字节\n' http://127.0.0.1:8000/js/vendor/echarts.min.js; echo -n '  /js/charts.js:             '; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/js/charts.js; else echo `"  ⚠ /api/health 返回 `$H，服务可能没起来，最近日志：`"; journalctl -u chat-agent -n 15 --no-pager; fi"

Write-Host ""
Write-Host "========================================"
Write-Host " 部署流程结束"
Write-Host "========================================"
Write-Host ""
Write-Host "回滚提示："
Write-Host "  代码备份: $AppDir/backups/code-backup-$Ts.tgz"
Write-Host "  Nginx 备份: $NginxConf.bak-$Ts（仅当检测到自定义改动时生成）"
Write-Host "  查看日志: ssh $Target 'journalctl -u chat-agent -n 50 --no-pager'"
Write-Host ""
Write-Host "浏览器验收：Ctrl+F5 强刷 → 上传数据文件 → 让它画图 → 刷新页面看图还在不在"
Write-Host ""
