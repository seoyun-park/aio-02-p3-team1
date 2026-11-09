param(
    [Parameter(Mandatory=$true)][string]$image_path,
    [Parameter(Mandatory=$true)][string]$output_path,
    [string]$language_tag = ''
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
$script:as_task_method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1
} | Select-Object -First 1
function wait_for_winrt_result($operation, [Type]$result_type) {
    $winrt_task = $script:as_task_method.MakeGenericMethod($result_type).Invoke($null, @($operation))
    $winrt_task.GetAwaiter().GetResult()
}
$image_file = wait_for_winrt_result ([Windows.Storage.StorageFile]::GetFileFromPathAsync($image_path)) ([Windows.Storage.StorageFile])
$image_stream = wait_for_winrt_result ($image_file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$image_decoder = wait_for_winrt_result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($image_stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$software_bitmap = wait_for_winrt_result ($image_decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
if ($language_tag) {
    [Windows.Globalization.Language, Windows.Globalization, ContentType=WindowsRuntime] | Out-Null
    $ocr_engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($language_tag))
} else {
    $ocr_engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if ($null -eq $ocr_engine) { throw 'No Windows OCR recognizer is installed.' }
$ocr_result = wait_for_winrt_result ($ocr_engine.RecognizeAsync($software_bitmap)) ([Windows.Media.Ocr.OcrResult])
$ocr_words = @($ocr_result.Lines | ForEach-Object {
    $_.Words | ForEach-Object {
        [pscustomobject]@{
            text = $_.Text
            x = $_.BoundingRect.X
            y = $_.BoundingRect.Y
            width = $_.BoundingRect.Width
            height = $_.BoundingRect.Height
        }
    }
})
$ocr_document = [pscustomobject]@{
    engine = 'Windows.Media.Ocr'
    language = $ocr_engine.RecognizerLanguage.LanguageTag
    width = $image_decoder.PixelWidth
    height = $image_decoder.PixelHeight
    text = $ocr_result.Text
    words = $ocr_words
}
$ocr_document | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $output_path -Encoding UTF8
$image_stream.Dispose()
Write-Output ('OCR words: ' + $ocr_words.Count)
