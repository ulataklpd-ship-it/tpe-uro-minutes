# Fetch Taipei urban renewal review board agenda/minutes list -> data.js
# ASCII only on purpose (Windows PowerShell 5.1 reads BOM-less files as ANSI).
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = 'Tls12'

$base  = 'https://uro.gov.taipei/cp.aspx?n=BB5DBFD036524472'
$pages = '27C1CA1F4D869A9C','16A084713E5FAE22','F1170526BBF294E2','DCC3A47133D797AA',
         '9B27A79A23B8ADCD','90A734A790E395B9','66EA86CEEF14420D'

function U([int[]]$codes) { return -join ($codes | ForEach-Object { [char]$_ }) }
$reDate  = '(\d+)\s*' + (U 0x5E74) + '\s*(\d+)\s*' + (U 0x6708) + '\s*(\d+)\s*' + (U 0x65E5)   # year / month / day
$reNo    = (U 0x7B2C) + '\s*(\d+)\s*' + (U 0x6B21)                # "di N ci"
$kwPanel = U 0x5C08,0x6848,0x5C0F,0x7D44                          # project panel
$pdfNote = '\(PDF' + (U 0x6A94,0x6848,0x4E0B,0x8F09) + '\)'       # "(PDF download)"

function Clean([string]$s) {
  $s = [regex]::Replace($s, '<[^>]+>', ' ')
  $s = [Net.WebUtility]::HtmlDecode($s)
  $s = [regex]::Replace($s, $pdfNote, '')
  return ([regex]::Replace($s, '\s+', ' ')).Trim()
}

function Get-Links([string]$cell) {
  $out = @()
  $ms = [regex]::Matches($cell, '(?s)(.*?)<a\b([^>]*)>(.*?)</a>')
  foreach ($m in $ms) {
    $href = [regex]::Match($m.Groups[2].Value, 'href="([^"]+)"').Groups[1].Value
    if (-not $href) { continue }
    $label = Clean $m.Groups[1].Value
    if (-not $label) { $label = Clean ([regex]::Match($m.Groups[2].Value, 'title="([^"]*)"').Groups[1].Value) }
    if (-not $label) { $label = Clean $m.Groups[3].Value }
    $out += [pscustomobject]@{ label = $label; url = [Net.WebUtility]::HtmlDecode($href) }
  }
  return ,$out
}

$all = @()
foreach ($p in $pages) {
  $html = (Invoke-WebRequest -UseBasicParsing "$base&s=$p").Content
  $rows = [regex]::Matches($html, '(?s)<tr[^>]*>(.*?)</tr>')
  $n = 0
  foreach ($r in $rows) {
    $cells = [regex]::Matches($r.Groups[1].Value, '(?s)<td[^>]*>(.*?)</td>')
    if ($cells.Count -lt 2) { continue }
    $d = [regex]::Match((Clean $cells[0].Groups[1].Value), $reDate)
    if (-not $d.Success) { continue }
    $iso = '{0:D4}-{1:D2}-{2:D2}' -f ([int]$d.Groups[1].Value + 1911), [int]$d.Groups[2].Value, [int]$d.Groups[3].Value
    $agendaText = Clean $cells[1].Groups[1].Value
    $minutesHtml = ''
    if ($cells.Count -ge 3) { $minutesHtml = $cells[2].Groups[1].Value }
    $both = $agendaText + ' ' + (Clean $minutesHtml)
    $no = [regex]::Match($both, $reNo).Groups[1].Value
    $type = 'board'
    if ($both.Contains($kwPanel)) { $type = 'panel' }
    $all += [pscustomobject]@{
      id      = "$iso-$type-$no"
      date    = $iso
      rocDate = (Clean $cells[0].Groups[1].Value)
      type    = $type
      no      = $no
      title   = $agendaText
      agenda  = (Get-Links $cells[1].Groups[1].Value)
      minutes = (Get-Links $minutesHtml)
    }
    $n++
  }
  Write-Host "page $p : $n rows"
}

$all  = $all | Sort-Object date -Descending
$json = ConvertTo-Json @($all) -Depth 6 -Compress
$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm')
$js = "window.FETCHED_AT = '$stamp';`r`nwindow.MEETINGS = $json;`r`n"
$outFile = Join-Path $PSScriptRoot 'data.js'
[IO.File]::WriteAllText($outFile, $js, (New-Object Text.UTF8Encoding($true)))
Write-Host "total $($all.Count) meetings -> $outFile"
