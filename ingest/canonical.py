"""中立事件层。分析器只吃这里的输出，不许直连宿主原始日志。"""
import re

CANON_VERSION = 6

RETRIEVAL = {
    "Read", "Grep", "Glob", "NotebookRead", "ToolSearch", "WebFetch", "WebSearch",
    "ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool",
    # Pi 内建工具（小写名，v6 增量）：find=按 glob 找文件、ls=列目录、grep/find/ls 默认关
    "read", "grep", "find", "ls",
}
MUTATION = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Artifact", "DesignSync",
            "write", "edit"}  # 后两个是 Pi
COORDINATION = {
    "TodoWrite", "Task", "Agent", "Workflow", "AskUserQuestion", "Skill",
    "ExitPlanMode", "EnterPlanMode", "SendMessage", "TaskOutput", "TaskStop",
    "Monitor", "ScheduleWakeup", "CronCreate", "CronList", "CronDelete",
    "EnterWorktree", "ExitWorktree", "ReportFindings", "PushNotification",
}
SHELL = {"Bash", "PowerShell", "bash"}  # bash 是 Pi；Pi 侧约一半工具调用是壳命令

# bash / pwsh 命令正则。顺序敏感：先校验、再执行、再变更、最后检索。
# 匹配前先剥离前缀（cd / Set-Location / env 赋值 / 变量赋值），否则一切都被 cd 遮蔽。
_PREFIX = re.compile(
    r"^(\s*(cd|Set-Location|pushd)\s+[^;&|\n]+[;&|]*\s*"
    r"|\s*\$env:\w+\s*=\s*[^;\n]+[;]?\s*"
    r"|\s*export\s+\w+=[^;\n]+[;&]*\s*"
    r"|\s*\[Console\]::\w+\s*=\s*[^;\n]+[;]?\s*"
    r")+", re.I)

_VERIFY = re.compile(
    r"\b(pytest|npm\s+(run\s+)?(test|build|lint|check|typecheck)|yarn\s+(test|build|lint|check)|"
    r"biome\s+(check|ci)|"
    r"tsc\b|eslint|ruff|flake8|mypy|go\s+(test|build|vet)|cargo\s+(test|build|clippy)|"
    r"mvn\b|gradlew?\b|make\s+(test|check|build)|dotnet\s+(test|build)|"
    r"python\s+-m\s+(pytest|unittest)|import\s+ast;\s*ast\.parse|"
    r"curl(\.exe)?\s+[^|\n]*(localhost|127\.0\.0\.1)|"
    r"Invoke-(Pester|WebRequest|RestMethod)[^|\n]*(localhost|127\.0\.0\.1)|"
    r"Get-NetTCPConnection|Test-NetConnection|netstat|check-services)", re.I)

_EXEC = re.compile(
    r"(^|[;&|(\n]\s*|\b(do|then|else)\s+|\$\w+\s*=\s*)("
    r"python3?(\.exe)?\s+(?!-m\s+(pytest|unittest))|"
    r"node(\.exe)?\s|java\s+-jar|npm\s+(start|run\s+(dev|serve))|npx\s|uvx?\s|deno\s|"
    r"uvicorn|gunicorn|flask\s+run|streamlit|"
    r"docker\s+(exec|compose|run|start|restart|stop)|docker-compose|kubectl|"
    r"&\s+['\"]?[^\s'\"]+\.ps1|pwsh\s|powershell(\.exe)?\s+-File|bash\s+\S+\.sh|sh\s+\S+\.sh|"
    r"Start-Process|Start-Service|Restart-Service|Stop-Process|Stop-Service|"
    r"schtasks\s+/run|Start-ScheduledTask|cloudflared|claude(\.exe)?\s|codex(\.exe)?\s|"
    r"conda\s+run|Invoke-Expression|Start-Job|code(\.exe)?\s"
    r")", re.I)

_MUTATE = re.compile(
    r"(^|[;&|\n]\s*|\b(do|then|else)\s+)(rm|mv|cp|mkdir|touch|chmod|chown|ln|sed\s+-i|tee|dd|"
    r"git\s+(add|commit|push|checkout|merge|rebase|reset|stash|tag)|"
    r"npm\s+(run\s+)?(i|install|publish|version)|pip\s+install|conda\s+(create|install|remove)|"
    r"schtasks\s+/(Create|Delete|Change)|New-ScheduledTask\w*|Register-ScheduledTask|Unregister-ScheduledTask|"
    r"New-Item|Remove-Item|Set-Content|Out-File|Copy-Item|Move-Item|Rename-Item|"
    r"Compress-Archive|Expand-Archive|attrib|icacls|reg\s+add|Set-ItemProperty|New-NetFirewallRule)\b|"
    r"(>>?\s*[^|&\s]|<<\s*'?[A-Z])", re.I)

_RETRIEVE = re.compile(
    r"(^|[;&|\n]\s*|\b(do|then|else)\s+)(cat|head|tail|less|grep|rg|find|ls|dir|wc|sed\s+-n|awk|jq|du|df|"
    r"stat|file|tree|which|where|echo|pwd|env|date|type\s|"
    r"git\s+(status|log|diff|show|branch|remote|config|check-ignore|ls-files)|"
    r"Get-\w+|Test-Path|Select-String|Measure-Object|conda\s+env\s+list|curl|Invoke-WebRequest|Invoke-RestMethod)\b", re.I)


def classify(name: str, raw: str | None) -> str:
    if name in RETRIEVAL:
        return "retrieval"
    if name in MUTATION:
        return "mutation"
    if name in COORDINATION:
        return "coordination"
    if name.startswith("mcp__"):
        if re.search(r"(search|fetch|extract|crawl|map|list|get|read|snapshot|research)", name, re.I):
            return "retrieval"
        return "other"
    if name in SHELL:
        if not raw:
            return "unknown"
        body = _PREFIX.sub("", raw)
        if _VERIFY.search(body):
            return "verification"
        if _EXEC.search(body):
            return "execution"
        if _MUTATE.search(body):
            return "mutation"
        if _RETRIEVE.search(body):
            return "retrieval"
        return "unknown"
    return "unknown"


def target_of(name: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "path", "notebook_path", "pattern", "url", "query", "skill", "prompt"):
        v = inp.get(k)
        if isinstance(v, str) and v:
            return v[:200]
    cmd = inp.get("command")
    if isinstance(cmd, str):
        return cmd.strip().split()[0][:200] if cmd.strip() else ""
    return ""
