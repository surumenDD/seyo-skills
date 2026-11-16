<#
.SYNOPSIS
  PowerPoint 自身に .pptx を PDF として書き出させる（check_pdf.py で測るための発表当日と同じ描画）。

.DESCRIPTION
  - 元の .pptx は読み取り専用・ウィンドウなしで開き、保存し直さない。
  - 非表示のスライドは既定では含めない（聴衆が見るスライドだけ）。-IncludeHidden で含める。
    どちらの場合も、非表示のスライドの番号を出力に書く（PDF のページ番号とスライドの番号がずれるため）。
  - このスクリプトが起動した PowerPoint は必ず終了させる。既に動いている PowerPoint は閉じない。
  - 同じファイルが PowerPoint で開かれているときは書き出さずに終了コード 3 で知らせる。

  使い方:
    powershell -NoProfile -ExecutionPolicy Bypass -File export_pdf.ps1 -Path deck.pptx -OutPdf deck.pdf [-IncludeHidden]

  終了コード: 0 成功 / 2 PowerPoint が無い / 3 ファイルが開かれている・開けない / 4 書き出しに失敗
#>
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$OutPdf,
    [switch]$IncludeHidden
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}

if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    Write-Output "入力のファイルが見つかりません: $Path"
    exit 3
}
$src = (Resolve-Path -LiteralPath $Path).ProviderPath
$out = [IO.Path]::GetFullPath($OutPdf)
if ($out -ieq $src) { Write-Output "出力先が入力と同じです。別の名前を指定してください。"; exit 4 }
$outDir = Split-Path -Parent $out
if (-not (Test-Path -LiteralPath $outDir)) { New-Item -ItemType Directory -Force -Path $outDir | Out-Null }

if (-not (Test-Path 'Registry::HKEY_CLASSES_ROOT\PowerPoint.Application')) {
    Write-Output "PowerPoint が見つかりません。PowerPoint（またはお使いのツール）で PDF に保存し、その PDF を check_pdf.py に渡してください。"
    exit 2
}

$before = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
$wasRunning = $before.Count -gt 0
try {
    $pp = New-Object -ComObject PowerPoint.Application
} catch {
    Write-Output "PowerPoint を起動できませんでした（$($_.Exception.Message)）。PowerPoint で PDF に保存し、その PDF を渡してください。"
    exit 2
}
# このスクリプトが起動した PowerPoint のプロセス（終了の確認に使う）
$ours = @()
if (-not $wasRunning) { $ours = @(Get-Process -Name POWERPNT -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }) }

$pres = $null
$code = 0
try {
    if ($wasRunning) {
        foreach ($p in $pp.Presentations) {
            if ($p.FullName -ieq $src) {
                Write-Output "このファイルは PowerPoint で開かれています。PowerPoint で［ファイル］→［エクスポート］→［PDF/XPS の作成］で保存してその PDF を渡すか、ファイルを閉じてから再実行してください。"
                $code = 3
                break
            }
        }
    }
    if ($code -eq 0) {
        try {
            # ReadOnly = msoTrue, Untitled = msoFalse, WithWindow = msoFalse
            $pres = $pp.Presentations.Open($src, -1, 0, 0)
        } catch {
            Write-Output "PowerPoint でファイルを開けませんでした（$($_.Exception.Message)）。別のプログラムが使用中か、パスワードや修復の確認が出ている可能性があります。PowerPoint で開いて PDF に保存し、その PDF を渡してください。"
            $code = 3
        }
    }
    if ($code -eq 0) {
        $hidden = @()
        foreach ($s in $pres.Slides) {
            if ($s.SlideShowTransition.Hidden -eq -1) { $hidden += $s.SlideIndex }
        }
        if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force -Confirm:$false }
        if ($IncludeHidden) {
            # 読み取り専用で開いた手元の写しの上だけで非表示を解く（元のファイルには保存しない）。
            # PDF への保存（SaveAs）は非表示のスライドを含めず、ExportAsFixedFormat の PrintHiddenSlides は
            # PowerShell からは効かなかったため、この方法をとる。
            foreach ($i in $hidden) { $pres.Slides.Item($i).SlideShowTransition.Hidden = 0 }
        }
        # ppSaveAsPDF = 32。PDF の保存は書き出しで、プレゼンテーション自体の保存先は変わらない。
        $pres.SaveAs($out, 32)
        if (-not (Test-Path -LiteralPath $out)) { throw "PDF ができていません" }
        $n = $pres.Slides.Count
        if ($hidden.Count -eq 0) {
            Write-Output "書き出しました: $out（$n 枚、非表示のスライドなし）"
        } elseif ($IncludeHidden) {
            Write-Output "書き出しました: $out（$n 枚、非表示のスライド $($hidden -join ', ') も含めた。ページ番号＝スライドの番号）"
        } else {
            Write-Output "書き出しました: $out（$($n - $hidden.Count) ページ。非表示のスライド $($hidden -join ', ') は含めていないので、それ以降はページ番号とスライドの番号がずれる）"
        }
    }
} catch {
    Write-Output "PDF への書き出しに失敗しました（$($_.Exception.Message)）。PowerPoint で PDF に保存し、その PDF を渡してください。"
    $code = 4
} finally {
    if ($pres -ne $null) {
        try { $pres.Close() } catch {}
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($pres)
    }
    if (-not $wasRunning) { try { $pp.Quit() } catch {} }
    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($pp)
    $pp = $null
    [GC]::Collect(); [GC]::WaitForPendingFinalizers()
    if (-not $wasRunning -and $ours.Count -gt 0) {
        # 起動したプロセスが残っていれば、それだけを終わらせる（既存の PowerPoint には触れない）
        $deadline = (Get-Date).AddSeconds(15)
        while ((Get-Date) -lt $deadline -and @(Get-Process -Id $ours -ErrorAction SilentlyContinue).Count -gt 0) {
            Start-Sleep -Milliseconds 300
        }
        Get-Process -Id $ours -ErrorAction SilentlyContinue | Stop-Process -Force -Confirm:$false
    }
}
exit $code
