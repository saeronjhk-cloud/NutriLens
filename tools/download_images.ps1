# NutriLens 테스트 이미지 다운로드 (PowerShell)
# 실행법:
#   NutriLens 폴더에서 마우스 우클릭 → "터미널에서 열기"
#   powershell -ExecutionPolicy Bypass -File tools\download_images.ps1

$targetDir = ".tmp\test_images"
if (-not (Test-Path $targetDir)) { New-Item -ItemType Directory -Path $targetDir -Force | Out-Null }

$foods = @(
    @{name="01_김치"; url="https://upload.wikimedia.org/wikipedia/commons/4/44/Korean_cuisine-Gimchi-01.jpg"},
    @{name="02_비빔밥"; url="https://upload.wikimedia.org/wikipedia/commons/4/44/Dolsot-bibimbap.jpg"},
    @{name="03_불고기"; url="https://upload.wikimedia.org/wikipedia/commons/0/0a/Korean_cuisine-Bulgogi-01.jpg"},
    @{name="04_김밥"; url="https://upload.wikimedia.org/wikipedia/commons/7/7e/Gimbap_%28pixabay%29.jpg"},
    @{name="05_떡볶이"; url="https://upload.wikimedia.org/wikipedia/commons/4/4d/Tteok-bokki.jpg"},
    @{name="06_된장찌개"; url="https://upload.wikimedia.org/wikipedia/commons/4/44/Korean_cuisine-Doenjang_jjigae-01.jpg"},
    @{name="07_삼겹살"; url="https://upload.wikimedia.org/wikipedia/commons/5/59/Korean_barbeque-Samgyeopsal-01.jpg"},
    @{name="08_잡채"; url="https://upload.wikimedia.org/wikipedia/commons/5/54/Korean_cuisine-Japchae-01.jpg"},
    @{name="09_순두부찌개"; url="https://upload.wikimedia.org/wikipedia/commons/3/31/Korean_cuisine-Sundubu_jjigae-01.jpg"},
    @{name="10_갈비탕"; url="https://upload.wikimedia.org/wikipedia/commons/9/9e/Korean_cuisine-Galbitang-01.jpg"},
    @{name="11_냉면"; url="https://upload.wikimedia.org/wikipedia/commons/a/ac/Korean_cuisine-Mul_naengmyeon-01.jpg"},
    @{name="12_김치찌개"; url="https://upload.wikimedia.org/wikipedia/commons/3/34/Korean_cuisine-Kimchi_jjigae-01.jpg"},
    @{name="13_제육볶음"; url="https://upload.wikimedia.org/wikipedia/commons/1/12/Korean_cuisine-Jeyuk_bokkeum-01.jpg"},
    @{name="14_파전"; url="https://upload.wikimedia.org/wikipedia/commons/e/e3/Korean_cuisine-Pajeon-01.jpg"},
    @{name="15_감자탕"; url="https://upload.wikimedia.org/wikipedia/commons/5/56/Gamjatang.jpg"},
    @{name="16_칼국수"; url="https://upload.wikimedia.org/wikipedia/commons/0/0c/Kalguksu.jpg"},
    @{name="17_삼계탕"; url="https://upload.wikimedia.org/wikipedia/commons/f/f8/Korean_cuisine-Samgyetang-01.jpg"},
    @{name="18_족발"; url="https://upload.wikimedia.org/wikipedia/commons/9/96/Korean_cuisine-Jokbal-01.jpg"},
    @{name="19_라면"; url="https://upload.wikimedia.org/wikipedia/commons/b/bc/Ramyeon.jpg"},
    @{name="20_닭갈비"; url="https://upload.wikimedia.org/wikipedia/commons/1/1a/Dakgalbi.jpg"},
    @{name="21_볶음밥"; url="https://upload.wikimedia.org/wikipedia/commons/2/2d/Bokkeumbap.jpg"},
    @{name="22_만두"; url="https://upload.wikimedia.org/wikipedia/commons/4/4b/Korean_cuisine-Mandu-01.jpg"},
    @{name="23_육회"; url="https://upload.wikimedia.org/wikipedia/commons/8/89/Korean_cuisine-Yukhoe-01.jpg"},
    @{name="24_떡국"; url="https://upload.wikimedia.org/wikipedia/commons/a/a2/Tteokguk.jpg"},
    @{name="25_순대"; url="https://upload.wikimedia.org/wikipedia/commons/6/69/Korean_blood_sausage-Sundae-01.jpg"},
    @{name="26_치킨"; url="https://upload.wikimedia.org/wikipedia/commons/5/5a/Korean_fried_chicken_%28cropped%29.jpg"},
    @{name="27_해물탕"; url="https://upload.wikimedia.org/wikipedia/commons/d/d2/Korean_cuisine-Haemultang-01.jpg"},
    @{name="28_오징어볶음"; url="https://upload.wikimedia.org/wikipedia/commons/0/0e/Korean_cuisine-Ojingeo_bokkeum-01.jpg"},
    @{name="29_떡"; url="https://upload.wikimedia.org/wikipedia/commons/3/3d/Korean_rice_cake-Tteok-13.jpg"},
    @{name="30_김치볶음밥"; url="https://upload.wikimedia.org/wikipedia/commons/8/81/Kimchi_bokkeumbap.jpg"},
    @{name="31_닭가슴살"; url="https://upload.wikimedia.org/wikipedia/commons/a/ae/Grilled_chicken_breast_%281%29.jpg"},
    @{name="32_샐러드"; url="https://upload.wikimedia.org/wikipedia/commons/9/94/Salad_platter.jpg"},
    @{name="33_고구마"; url="https://upload.wikimedia.org/wikipedia/commons/5/5e/GunGoguma.jpg"},
    @{name="34_계란"; url="https://upload.wikimedia.org/wikipedia/commons/4/44/Boiled_eggs.jpg"},
    @{name="35_현미밥"; url="https://upload.wikimedia.org/wikipedia/commons/f/fa/Brown_rice.jpg"},
    @{name="36_오트밀"; url="https://upload.wikimedia.org/wikipedia/commons/d/d2/Porridge.jpg"},
    @{name="37_연어"; url="https://upload.wikimedia.org/wikipedia/commons/3/39/SalmonSashimi.jpg"},
    @{name="38_아보카도"; url="https://upload.wikimedia.org/wikipedia/commons/6/6d/Good_Food_Display_-_NCI_Visuals_Online.jpg"},
    @{name="39_두부"; url="https://upload.wikimedia.org/wikipedia/commons/0/03/Korean_cuisine-Dubu-01.jpg"},
    @{name="40_바나나"; url="https://upload.wikimedia.org/wikipedia/commons/8/8a/Banana-Single.jpg"},
    @{name="41_프로틴쉐이크"; url="https://upload.wikimedia.org/wikipedia/commons/6/6d/Protein_shake.jpg"},
    @{name="42_그릭요거트"; url="https://upload.wikimedia.org/wikipedia/commons/e/ea/Turkish_strained_yogurt.jpg"},
    @{name="43_스테이크"; url="https://upload.wikimedia.org/wikipedia/commons/4/4c/Filet_Mignon_Beef_Steak.jpg"},
    @{name="44_브로콜리"; url="https://upload.wikimedia.org/wikipedia/commons/0/03/Broccoli_and_cross_section_edit.jpg"},
    @{name="45_견과류"; url="https://upload.wikimedia.org/wikipedia/commons/1/10/Mixed_nuts_small_pile.jpg"}
)

Write-Host ""
Write-Host "======================================================="
Write-Host "  NutriLens 테스트 이미지 다운로드"
Write-Host "======================================================="
Write-Host ""

$success = 0
$fail = 0
$total = $foods.Count

foreach ($i in 0..($total-1)) {
    $f = $foods[$i]
    $outPath = Join-Path $targetDir "$($f.name).jpg"

    if (Test-Path $outPath) {
        Write-Host "  [$($i+1)/$total] $($f.name) - 이미 있음 (스킵)"
        $success++
        continue
    }

    Write-Host -NoNewline "  [$($i+1)/$total] $($f.name) 다운로드 중... "

    try {
        $webClient = New-Object System.Net.WebClient
        $webClient.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        $webClient.DownloadFile($f.url, (Resolve-Path $targetDir).Path + "\$($f.name).jpg")
        $fileSize = [math]::Round((Get-Item $outPath).Length / 1024)
        Write-Host "OK (${fileSize}KB)"
        $success++
    } catch {
        Write-Host "실패: $_"
        $fail++
    }

    Start-Sleep -Milliseconds 300
}

Write-Host ""
Write-Host "  완료! 성공: $success, 실패: $fail"
Write-Host "  저장 위치: $targetDir"
Write-Host ""
