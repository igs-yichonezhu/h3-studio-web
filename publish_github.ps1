$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
& gh auth status
if ($LASTEXITCODE -ne 0) { throw 'Run gh auth login with yichonezhu, then run this script again.' }
$taskLogin = (& gh api user --jq .login).Trim()
if ($LASTEXITCODE -ne 0 -or $taskLogin -ne 'yichonezhu') { throw 'Please sign in as yichonezhu before publishing.' }
if (& git status --porcelain) { throw 'Review and commit local changes before publishing.' }
$taskRepo = 'yichonezhu/h3-studio-web'
& gh repo view $taskRepo --json nameWithOwner 2>$null
if ($LASTEXITCODE -ne 0) {
    & gh repo create $taskRepo --public --description 'H3 Studio web interface for company LAN access' --source . --remote origin
    if ($LASTEXITCODE -ne 0) { throw 'Could not create GitHub repository.' }
}
$taskRemote = & git remote get-url origin 2>$null
if ($LASTEXITCODE -ne 0) {
    & git remote add origin 'https://github.com/yichonezhu/h3-studio-web.git'
} elseif ($taskRemote -notin @('https://github.com/yichonezhu/h3-studio-web.git', 'git@github.com:yichonezhu/h3-studio-web.git')) {
    throw 'The origin remote does not match the intended GitHub repository.'
}
& gh auth setup-git
if ($LASTEXITCODE -ne 0) { throw 'Could not configure GitHub authentication for Git.' }
& git push --set-upstream origin main
if ($LASTEXITCODE -ne 0) { throw 'Could not push main. Preserve changes and resolve the reported Git error.' }
& gh api "repos/$taskRepo/pages" --method POST -f build_type=workflow 2>$null
if ($LASTEXITCODE -ne 0) {
    & gh api "repos/$taskRepo/pages" --method PUT -f build_type=workflow
    if ($LASTEXITCODE -ne 0) { throw 'Could not enable GitHub Pages. Check account permissions and plan.' }
}
& gh workflow run pages.yml --repo $taskRepo --ref main
if ($LASTEXITCODE -ne 0) { throw 'Could not start the Pages workflow. Check Actions permissions.' }
Write-Host 'Repository pushed. Check deployment:'
& gh run list --repo $taskRepo --workflow pages.yml --limit 1
Write-Host 'Website after successful deployment: https://yichonezhu.github.io/h3-studio-web/'
