# Replace the placeholders in this repo with your own identity.
#
#   ./tools/personalize.ps1 -GitHubUser myuser -DisplayName "My Name"
#
# .git/, .github/ and tools/ are skipped on purpose: the CI guard and this script
# itself contain the placeholder strings and must keep working afterwards.
param(
  [Parameter(Mandatory=$true)][string]$GitHubUser,
  [Parameter(Mandatory=$true)][string]$DisplayName
)
$root = Split-Path -Parent $PSScriptRoot
Get-ChildItem -Path $root -Recurse -File |
  Where-Object { $_.FullName -notmatch '\\(\.git|\.github|tools)\\' } |
  ForEach-Object {
    $t = Get-Content -Raw -LiteralPath $_.FullName
    if ($t -match 'YOUR_GITHUB_USERNAME|YOUR_NAME') {
      $t = $t -replace 'YOUR_GITHUB_USERNAME', $GitHubUser -replace 'YOUR_NAME', $DisplayName
      Set-Content -LiteralPath $_.FullName -Value $t -NoNewline
      "updated $($_.FullName)"
    }
  }
"done -- review 'git diff' before committing."
