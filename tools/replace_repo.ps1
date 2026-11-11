# Knowledge Battle 저장소 전체 교체 (Git 메타데이터 보존; 예전 파일 백업 생성)
param([Parameter(Mandatory=$true)][string]$RepoPath)
$ErrorActionPreference='Stop'
$source = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$target = (Resolve-Path $RepoPath).Path
if (-not (Test-Path (Join-Path $target '.git'))) { throw "경고: .git이 없는 경로입니다. GitHub 저장소의 로컬 경로를 지정하세요." }
if ($target -eq $source -or $source.StartsWith($target + [IO.Path]::DirectorySeparatorChar)) { throw "교체 원본 폴더가 대상 저장소 안에 있습니다. ZIP을 저장소 바깥에 압축 해제하세요." }
$backup="$target-knowledge-battle-backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Path $backup | Out-Null
Get-ChildItem -LiteralPath $target -Force | Where-Object {$_.Name -ne '.git'} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $backup -Recurse -Force
}
Get-ChildItem -LiteralPath $target -Force | Where-Object {$_.Name -ne '.git'} | Remove-Item -Recurse -Force
Get-ChildItem -LiteralPath $source -Force | Where-Object {$_.Name -notin @('node_modules','__pycache__','.firebase')} | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $target -Recurse -Force
}
Write-Host "완료: Knowledge Battle 파일로 교체했습니다. 기존 파일 백업: $backup" -ForegroundColor Green
Write-Host 'GitHub Desktop에서 Changes 확인 후 Commit / Push하세요.' -ForegroundColor Cyan
