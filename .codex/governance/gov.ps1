param(
    [Parameter(Position=0, Mandatory=$true)]
    [string]$Command,
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
$Script = Join-Path $PSScriptRoot "gov.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -X utf8 $Script $Command @Rest
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $Script $Command @Rest
    exit $LASTEXITCODE
}
throw "Python 3 is required for Codex governance scripts."
