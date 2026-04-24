$branch = "claude/extract-pdf-data-OvLxX"
$batchSize = 50
git checkout $branch

$hunterFiles = Get-ChildItem -Path "data\product_data\hunter" -Recurse -File | Where-Object { $_.Name -ne ".gitkeep" }
Write-Host "Found $($hunterFiles.Count) Hunter files"

for ($i = 0; $i -lt $hunterFiles.Count; $i += $batchSize) {
    $batch = $hunterFiles[$i..([Math]::Min($i + $batchSize - 1, $hunterFiles.Count - 1))]
    $batchNum = [Math]::Floor($i / $batchSize) + 1
    $totalBatches = [Math]::Ceiling($hunterFiles.Count / $batchSize)
    foreach ($file in $batch) { git add $file.FullName }
    git commit -m "Add Hunter PDFs batch $batchNum/$totalBatches ($($batch.Count) files)"
    $pushed = $false; $wait = 2
    for ($r = 0; $r -lt 4; $r++) {
        git push -u origin $branch
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Write-Host "Push failed, retrying in $wait seconds..."
        Start-Sleep -Seconds $wait; $wait *= 2
    }
    if (-not $pushed) { Write-Host "ERROR: Failed to push Hunter batch $batchNum"; exit 1 }
    Write-Host "Pushed Hunter batch $batchNum/$totalBatches"
}

$coatsFiles = Get-ChildItem -Path "data\product_data\coats" -Recurse -File | Where-Object { $_.Name -ne ".gitkeep" }
Write-Host "Found $($coatsFiles.Count) Coats files"

for ($i = 0; $i -lt $coatsFiles.Count; $i += $batchSize) {
    $batch = $coatsFiles[$i..([Math]::Min($i + $batchSize - 1, $coatsFiles.Count - 1))]
    $batchNum = [Math]::Floor($i / $batchSize) + 1
    $totalBatches = [Math]::Ceiling($coatsFiles.Count / $batchSize)
    foreach ($file in $batch) { git add $file.FullName }
    git commit -m "Add Coats PDFs batch $batchNum/$totalBatches ($($batch.Count) files)"
    $pushed = $false; $wait = 2
    for ($r = 0; $r -lt 4; $r++) {
        git push -u origin $branch
        if ($LASTEXITCODE -eq 0) { $pushed = $true; break }
        Write-Host "Push failed, retrying in $wait seconds..."
        Start-Sleep -Seconds $wait; $wait *= 2
    }
    if (-not $pushed) { Write-Host "ERROR: Failed to push Coats batch $batchNum"; exit 1 }
    Write-Host "Pushed Coats batch $batchNum/$totalBatches"
}

Write-Host "Done! All PDFs pushed."
