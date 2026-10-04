$ErrorActionPreference = 'Stop'
$key = (Get-Content "$PSScriptRoot\AgentDemo\.env" | Where-Object { $_ -match '^API_KEY=' }) -replace '^API_KEY=', ''
$endpoint = 'https://api.z.ai/api/coding/paas/v4/chat/completions'

$system = @'
Ты — вежливый универсальный ассистент. Отвечай кратко, ясно и по делу на русском языке.
Если вопрос неоднозначен — уточни. Не выдумывай факты.
'@

function Estimate-Text($t) {
    if ([string]::IsNullOrWhiteSpace($t)) { return 0 }
    $lat = 0; $cyr = 0; $oth = 0
    foreach ($ch in $t.ToCharArray()) {
        if ([char]::IsWhiteSpace($ch)) { continue }
        elseif ((($ch -ge 'a') -and ($ch -le 'z')) -or (($ch -ge 'A') -and ($ch -le 'Z')) -or [char]::IsDigit($ch)) { $lat++ }
        elseif ((($ch -ge [char]0x0430) -and ($ch -le [char]0x044F)) -or (($ch -ge [char]0x0410) -and ($ch -le [char]0x042F)) -or ($ch -eq [char]0x0451) -or ($ch -eq [char]0x0401)) { $cyr++ }
        else { $oth++ }
    }
    return [int][math]::Round($lat / 4.0 + $cyr / 2.8 + $oth / 1.0)
}
function Estimate-Request($msgs) {
    $s = 0
    foreach ($m in $msgs) { $s += (Estimate-Text $m.content) + 4 }
    return $s + 3
}

$userTurns = @(
    'Привет! Как тебя зовут?',
    'Объясни простыми словами, что такое контекстное окно у языковой модели.',
    'А почему при длинном диалоге ответы становятся дороже? Разложи по токенам.',
    'Приведи три практических способа уменьшить расход токенов в чат-агенте.',
    'Спасибо. Теперь кратко повтори всё, что мы обсудили, одним абзацем.'
)

$messages = New-Object System.Collections.ArrayList
[void]$messages.Add(@{ role = 'system'; content = $system })

$cumTotal = 0; $cumCost = 0.0
Write-Output ("{0,-4} {1,8} {2,8} {3,8} {4,10} {5,10} {6,12} {7,9}" -f 'turn','est_pr','prompt','compl','total','cumTotal','cumCost$','err%')

for ($i = 0; $i -lt $userTurns.Count; $i++) {
    $q = $userTurns[$i]
    [void]$messages.Add(@{ role = 'user'; content = $q })

    $estPrompt = Estimate-Request $messages

    $bodyObj = @{ model = 'glm-4.6'; messages = $messages; temperature = 0.7; thinking = @{ type = 'disabled' } }
    $json = $bodyObj | ConvertTo-Json -Depth 6
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)

    $resp = Invoke-RestMethod -Uri $endpoint -Method Post -Headers @{ Authorization = "Bearer $key" } -ContentType 'application/json' -Body $bytes
    $u = $resp.usage
    $answer = $resp.choices[0].message.content
    [void]$messages.Add(@{ role = 'assistant'; content = $answer })

    $cached = 0; if ($u.prompt_tokens_details) { $cached = [int]$u.prompt_tokens_details.cached_tokens }
    $cost = ($u.prompt_tokens - $cached) * 0.6 / 1e6 + $cached * 0.11 / 1e6 + $u.completion_tokens * 2.2 / 1e6
    $cumTotal += [int]$u.total_tokens
    $cumCost += $cost
    $err = ($estPrompt - $u.prompt_tokens) * 100.0 / $u.prompt_tokens

    Write-Output ("{0,-4} {1,8} {2,8} {3,8} {4,10} {5,10} {6,12:F6} {7,9:F1}" -f ($i+1), $estPrompt, $u.prompt_tokens, $u.completion_tokens, $u.total_tokens, $cumTotal, $cumCost, $err)
}
Write-Output "--- last answer preview ---"
Write-Output ($messages[$messages.Count-1].content.Substring(0, [math]::Min(200, $messages[$messages.Count-1].content.Length)))
