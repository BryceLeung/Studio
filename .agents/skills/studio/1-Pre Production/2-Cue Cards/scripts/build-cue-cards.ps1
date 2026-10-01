param(
    [Parameter(Mandatory = $true)] [string]$ProjectPath,
    [string]$OutputRoot,
    [string]$RenderRoot,
    [switch]$Overwrite
)

$ErrorActionPreference = 'Stop'

$project = (Resolve-Path -LiteralPath $ProjectPath).Path
$cueRoot = Join-Path $project '1-Pre Production\2-Cue Cards'
$working = Join-Path $cueRoot 'working'
$approved = Join-Path $project '1-Pre Production\1-Script Review\Approved'
$plans = @(Get-ChildItem -LiteralPath $working -Filter '*.json' -File | Sort-Object Name)
$scripts = @(Get-ChildItem -LiteralPath $approved -Filter '*.docx' -File | Sort-Object Name)
if ($scripts.Count -eq 0 -or $plans.Count -ne $scripts.Count) {
    throw "The approved script count ($($scripts.Count)) differs from the cue-card plan count ($($plans.Count))"
}
foreach ($script in $scripts) {
    if (-not (Test-Path -LiteralPath (Join-Path $working ($script.BaseName + '.json')))) {
        throw "Missing cue-card plan for $($script.Name)"
    }
}
$destination = if ($OutputRoot) { $OutputRoot } else { $cueRoot }
$renderDestination = if ($RenderRoot) { $RenderRoot } else { Join-Path $working ('render-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
New-Item -ItemType Directory -Path $destination, $renderDestination -Force | Out-Null

function Add-TextBox($slide, $text, $x, $y, $width, $height, $size, $bold, $align) {
    $box = $slide.Shapes.AddTextbox(1, $x, $y, $width, $height)
    $box.Fill.Visible = 0
    $box.Line.Visible = 0
    $box.TextFrame.MarginLeft = 0
    $box.TextFrame.MarginRight = 0
    $box.TextFrame.MarginTop = 0
    $box.TextFrame.MarginBottom = 0
    $box.TextFrame.WordWrap = -1
    $box.TextFrame.AutoSize = 0
    $box.TextFrame.VerticalAnchor = 3
    $box.TextFrame.TextRange.Text = $text.Replace("`n", "`r")
    $box.TextFrame.TextRange.Font.Name = 'Arial'
    $box.TextFrame.TextRange.Font.Size = $size
    $box.TextFrame.TextRange.Font.Bold = $(if ($bold) { -1 } else { 0 })
    $box.TextFrame.TextRange.Font.Color.RGB = 16777215
    $box.TextFrame.TextRange.ParagraphFormat.Alignment = $align
    $box.Left = $x
    $box.Top = $y
    $box.Width = $width
    $box.Height = $height
    return $box
}

function Add-Background($slide) {
    $background = $slide.Shapes.AddShape(1, 0, 0, 960, 540)
    $background.Fill.Solid()
    $background.Fill.ForeColor.RGB = 1250588
    $background.Line.Visible = 0
}

function Add-CueSlide($presentation, [string]$text, [string]$title) {
    $slide = $presentation.Slides.Add($presentation.Slides.Count + 1, 12)
    Add-Background $slide
    $cueBox = Add-TextBox $slide $text 65 52 830 426 36 $false 1
    if ($cueBox.TextFrame.TextRange.BoundHeight -le ($cueBox.Height - 12)) {
        return
    }

    $slide.Delete()
    $lines = @([regex]::Split($text, "`n"))
    if ($lines.Count -gt 1) {
        $middle = [int][math]::Ceiling($lines.Count / 2)
        $first = ($lines[0..($middle - 1)] -join "`n").Trim()
        $second = ($lines[$middle..($lines.Count - 1)] -join "`n").Trim()
    }
    else {
        $sentences = @([regex]::Split($text, '(?<=[.!?…])\s+(?=[A-Z“‘])'))
        if ($sentences.Count -gt 1) {
        $middle = [int][math]::Ceiling($sentences.Count / 2)
        $first = ($sentences[0..($middle - 1)] -join ' ')
        $second = ($sentences[$middle..($sentences.Count - 1)] -join ' ')
        }
        else {
            $words = @($text -split '\s+')
            if ($words.Count -lt 2) { throw "One word cannot fit on a cue slide: $title" }
            $middle = [int][math]::Ceiling($words.Count / 2)
            $first = ($words[0..($middle - 1)] -join ' ')
            $second = ($words[$middle..($words.Count - 1)] -join ' ')
        }
    }
    if (-not $first -or -not $second) { throw "Unable to split an overflowing cue slide: $title" }
    Add-CueSlide $presentation $first $title
    Add-CueSlide $presentation $second $title
}

$powerPoint = New-Object -ComObject PowerPoint.Application
try {
    foreach ($planFile in $plans) {
        $plan = Get-Content -LiteralPath $planFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $source = Join-Path $approved ($planFile.BaseName + '.docx')
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $source).Hash -ne $plan.source_sha256) {
            throw "Approved script changed after planning: $source"
        }
        $output = Join-Path $destination ($planFile.BaseName + '.pptx')
        if ((Test-Path -LiteralPath $output) -and -not $Overwrite) {
            throw "Existing deck requires review before overwrite: $output"
        }
        $presentation = $powerPoint.Presentations.Add()
        try {
            $presentation.PageSetup.SlideWidth = 960
            $presentation.PageSetup.SlideHeight = 540
            $titleSlide = $presentation.Slides.Add(1, 12)
            Add-Background $titleSlide
            [void](Add-TextBox $titleSlide $plan.title 70 145 820 210 54 $true 2)
            [void](Add-TextBox $titleSlide 'CUE CARDS' 70 375 820 45 19 $false 2)

            foreach ($text in $plan.slides) {
                Add-CueSlide $presentation ([string]$text) $plan.title
            }
            $cueCount = $presentation.Slides.Count - 1
            for ($index = 0; $index -lt $cueCount; $index++) {
                $slide = $presentation.Slides.Item($index + 2)
                [void](Add-TextBox $slide ('{0:D2} / {1:D2}' -f ($index + 1), $cueCount) 800 485 88 30 16 $false 3)
            }

            $presentation.SaveAs($output, 24)
            $renderDir = Join-Path $renderDestination $planFile.BaseName
            New-Item -ItemType Directory -Path $renderDir -Force | Out-Null
            foreach ($slide in $presentation.Slides) {
                $png = Join-Path $renderDir ('slide-{0:D3}.png' -f $slide.SlideIndex)
                $slide.Export($png, 'PNG', 960, 540)
            }
            Write-Output "Created $output with $($presentation.Slides.Count) slides"
        }
        finally {
            $presentation.Close()
        }
    }
}
finally {
    $powerPoint.Quit()
}
