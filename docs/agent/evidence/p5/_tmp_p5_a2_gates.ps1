# P5-A2 关卡执行：G1 语法 + G2 全量回归 + G3 覆盖率 + 端到端
# 结果写进 docs/agent/evidence/p5/，并打印一行汇总
$ErrorActionPreference = "Continue"
$root = "E:\程序\桌面宠物\xiaoyi-vrm-worktree"
Set-Location $root
$out = Join-Path $root "docs\agent\evidence\p5"
New-Item -ItemType Directory -Force -Path $out | Out-Null

function Run-Step($name, $args) {
    $log = Join-Path $out "$name.txt"
    Write-Host "=== $name => $log"
    & python @args *> $log
    $code = $LASTEXITCODE
    $tail = Get-Content $log -Tail 3 | Out-String
    Write-Host "[$name] exit=$code"
    Write-Host $tail
    return $code
}

# G1 语法
$files = @()
$files += Get-ChildItem -Path agent,core,tools,tests -Recurse -Filter *.py |
    Where-Object { $_.Name -notlike "_tmp_*" } | ForEach-Object { $_.FullName }
$synLog = Join-Path $out "g1_py_compile.txt"
$bad = 0
foreach ($f in $files) {
    & python -m py_compile $f 2>> $synLog
    if ($LASTEXITCODE -ne 0) { $bad++; Add-Content $synLog "FAIL $f" }
}
Add-Content $synLog "checked=$($files.Count) failed=$bad"
Write-Host "[G1] checked=$($files.Count) failed=$bad exit=$bad"

# G2 + G3 一次跑出
$covLog = Join-Path $out "g2_g3_tests_coverage.txt"
& python -m pytest tests -q --cov=core.kernel --cov=agent --cov=services.ack_cache --cov-report=term-missing *> $covLog
$covExit = $LASTEXITCODE
Write-Host "[G2/G3] exit=$covExit"
Get-Content $covLog | Select-String -Pattern "passed|failed|error" | Select-Object -Last 3
Get-Content $covLog | Select-String -Pattern "^TOTAL"

# 端到端三项
$e2e = @(
    @("p1_acceptance_smoke.py"),
    @("p3_acceptance_smoke.py"),
    @("measure_acceptance_metrics.py")
)
foreach ($script in $e2e) {
    $n = [System.IO.Path]::GetFileNameWithoutExtension($script[0])
    $log = Join-Path $out "$n.txt"
    & python (Join-Path $root "tools\$($script[0])") *> $log
    Write-Host "[e2e $n] exit=$LASTEXITCODE"
    Get-Content $log -Tail 2
}
Write-Host "DONE"
