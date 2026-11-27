"""Windows Authenticode 代码签名工具（长期维护设计：本地 starmiaoa 证书 & CI Secret 无缝切换）。

支持三种签名来源（按优先级）：
1. CI / 正式 CA 证书环境变量：`CODESIGN_PFX_BASE64` + `CODESIGN_PFX_PASSWORD`
   （未来接入 SignPath / Certum OSS / 商业代码签名证书时，只需替换 GitHub Secret，零代码改动）
2. 本地 PFX 证书：`certs/starmiaoa_codesign.pfx` + 密码（`CODESIGN_PFX_PASSWORD` 或 `certs/starmiaoa_codesign.pass`）
3. 首次本地初始化（`--init-self-signed` 或 `--auto-init`）：自动生成 10 年期 RSA-4096 `CN=starmiaoa`
   自签名代码签名证书到 gitignore 保护的 `certs/` 目录（绝不修改系统受信任根存储，也绝不上传私钥）。

统一使用 SHA-256 + RFC 3161 时间戳服务器签名，确保证书到期后历史版本签名仍保持可验证。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CERTS_DIR = PROJECT_ROOT / "certs"
DEFAULT_PFX_PATH = CERTS_DIR / "starmiaoa_codesign.pfx"
DEFAULT_CER_PATH = CERTS_DIR / "starmiaoa_codesign.cer"
DEFAULT_PASS_PATH = CERTS_DIR / "starmiaoa_codesign.pass"

DEFAULT_PUBLISHER = "starmiaoa"
TIMESTAMP_SERVERS = (
    "http://timestamp.digicert.com",
    "http://timestamp.sectigo.com",
)


def _run_ps(script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
    )


def ensure_local_self_signed_cert(
    pfx_path: Path = DEFAULT_PFX_PATH,
    cer_path: Path = DEFAULT_CER_PATH,
    pass_path: Path = DEFAULT_PASS_PATH,
    publisher: str = DEFAULT_PUBLISHER,
) -> tuple[Path, str]:
    """确保本地存在 10 年期 `CN=starmiaoa` RSA-4096 代码签名证书并返回 `(pfx_path, password)`。"""
    if pfx_path.is_file():
        pwd = os.environ.get("CODESIGN_PFX_PASSWORD", "").strip()
        if not pwd and pass_path.is_file():
            pwd = pass_path.read_text(encoding="utf-8").strip()
        if pwd:
            return pfx_path, pwd

    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    pwd = os.environ.get("CODESIGN_PFX_PASSWORD", "").strip() or secrets.token_urlsafe(24)

    ps_script = r"""
$ErrorActionPreference = 'Stop'
$subject = "CN=$env:CS_PUBLISHER, O=$env:CS_PUBLISHER, OU=WoWs-Mini-Relpay-Ru"
$friendly = "$env:CS_PUBLISHER Code Signing"
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $subject `
    -FriendlyName $friendly `
    -KeyAlgorithm RSA `
    -KeyLength 4096 `
    -HashAlgorithm SHA256 `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears(10)
$secPwd = ConvertTo-SecureString -String $env:CS_PWD -Force -AsPlainText
Export-PfxCertificate -Cert $cert -FilePath $env:CS_PFX -Password $secPwd | Out-Null
Export-Certificate -Cert $cert -FilePath $env:CS_CER -Type CERT | Out-Null
# 清理临时导入 CurrentUser\My 的副本，统一由受 gitignore 保护的 PFX 文件管理
Remove-Item -Path $cert.PSPath -Force -ErrorAction SilentlyContinue
Write-Output $cert.Thumbprint
"""
    res = _run_ps(
        ps_script,
        env={
            "CS_PUBLISHER": publisher,
            "CS_PWD": pwd,
            "CS_PFX": str(pfx_path),
            "CS_CER": str(cer_path),
        },
    )
    if res.returncode != 0:
        raise RuntimeError(f"生成本地代码签名证书失败:\n{res.stderr or res.stdout}")

    pass_path.write_text(pwd, encoding="utf-8")
    thumb = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    print(f"[Sign] 已生成 10 年期代码签名证书 (Subject: CN={publisher}, Thumbprint: {thumb})")
    print(f"       私钥 PFX (仅本地留存，已 gitignore): {pfx_path}")
    print(f"       公钥 CER (可选本地信任验证):       {cer_path}")
    return pfx_path, pwd


def inspect_signature(exe_path: Path) -> dict[str, str]:
    """读取并解析 Windows EXE 的 Authenticode 签名元数据。"""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$sig = Get-AuthenticodeSignature -FilePath $env:CS_TARGET
$obj = [ordered]@{
    Status = [string]$sig.Status
    StatusMessage = [string]$sig.StatusMessage
    SignerSubject = if ($sig.SignerCertificate) { [string]$sig.SignerCertificate.Subject } else { "" }
    SignerThumbprint = if ($sig.SignerCertificate) { [string]$sig.SignerCertificate.Thumbprint } else { "" }
    SignerNotAfter = if ($sig.SignerCertificate) { $sig.SignerCertificate.NotAfter.ToString("yyyy-MM-dd") } else { "" }
    TimeStamper = if ($sig.TimeStamperCertificate) { [string]$sig.TimeStamperCertificate.Subject } else { "" }
    HashAlgorithm = if ($sig.SignerCertificate) { [string]$sig.SignerCertificate.SignatureAlgorithm.FriendlyName } else { "" }
}
$obj | ConvertTo-Json -Compress
"""
    res = _run_ps(ps_script, env={"CS_TARGET": str(exe_path)})
    if res.returncode != 0 or not res.stdout.strip():
        return {"Status": "Error", "StatusMessage": res.stderr.strip()}
    try:
        return json.loads(res.stdout.strip())
    except json.JSONDecodeError:
        return {"Status": "Unknown", "StatusMessage": res.stdout.strip()}


def _apply_signature_with_pfx(exe_path: Path, pfx_path: Path, password: str) -> dict[str, str]:
    """使用 PFX 证书对目标二进制写入 SHA-256 Authenticode 签名（自动尝试 RFC 3161 时间戳）。"""
    ps_script = r"""
$ErrorActionPreference = 'Stop'
$secPwd = ConvertTo-SecureString -String $env:CS_PWD -Force -AsPlainText
$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2
$cert.Import($env:CS_PFX, $env:CS_PWD, [System.Security.Cryptography.X509Certificates.X509KeyStorageFlags]::Exportable)
$params = @{
    FilePath = $env:CS_TARGET
    Certificate = $cert
    HashAlgorithm = 'SHA256'
}
if ($env:CS_TSA) {
    $params['TimestampServer'] = $env:CS_TSA
}
$res = Set-AuthenticodeSignature @params
if ($res.Status -eq 'HashMismatch' -or $res.Status -eq 'NotSigned') {
    throw "Set-AuthenticodeSignature failed: $($res.Status) - $($res.StatusMessage)"
}
Write-Output "$($res.Status)|$($res.SignerCertificate.Subject)"
"""
    last_err = ""
    for tsa in (*TIMESTAMP_SERVERS, ""):
        res = _run_ps(
            ps_script,
            env={
                "CS_TARGET": str(exe_path),
                "CS_PFX": str(pfx_path),
                "CS_PWD": password,
                "CS_TSA": tsa,
            },
        )
        if res.returncode == 0:
            info = inspect_signature(exe_path)
            if info.get("SignerSubject"):
                return info
        last_err = (res.stderr or res.stdout).strip()
        if tsa:
            print(f"[Sign] 时间戳服务器 {tsa} 不可达，尝试备用节点...")
    raise RuntimeError(f"代码签名失败: {last_err}")


def sign_executable(
    exe_path: Path,
    *,
    auto_init_self_signed: bool = False,
    allow_unsigned: bool = False,
    publisher: str = DEFAULT_PUBLISHER,
) -> dict[str, str] | None:
    """对目标 Windows EXE 进行 Authenticode 签名。返回签名信息字典，跳过签名时返回 None。"""
    if sys.platform != "win32":
        print("[Sign] 非 Windows 环境，跳过 Authenticode 签名。")
        return None
    if not exe_path.is_file():
        raise FileNotFoundError(f"未找到待签名文件: {exe_path}")

    # 1. CI / 环境变量注入 PFX（优先，适配未来正式 CA / SignPath / Certum OSS 证书）
    b64_pfx = os.environ.get("CODESIGN_PFX_BASE64", "").strip()
    env_pwd = os.environ.get("CODESIGN_PFX_PASSWORD", "").strip()
    if b64_pfx:
        with tempfile.TemporaryDirectory(prefix="wows_sign_") as td:
            tmp_pfx = Path(td) / "signing.pfx"
            tmp_pfx.write_bytes(base64.b64decode(b64_pfx))
            try:
                return _apply_signature_with_pfx(exe_path, tmp_pfx, env_pwd)
            finally:
                try:
                    tmp_pfx.unlink(missing_ok=True)
                except OSError:
                    pass

    # 2. 本地 certs/starmiaoa_codesign.pfx
    pfx_env = os.environ.get("CODESIGN_PFX_PATH", "").strip()
    pfx_path = Path(pfx_env) if pfx_env else DEFAULT_PFX_PATH
    if pfx_path.is_file():
        pwd = env_pwd
        if not pwd and DEFAULT_PASS_PATH.is_file():
            pwd = DEFAULT_PASS_PATH.read_text(encoding="utf-8").strip()
        return _apply_signature_with_pfx(exe_path, pfx_path, pwd)

    # 3. 本地自动初始化自签名证书
    if auto_init_self_signed:
        gen_pfx, gen_pwd = ensure_local_self_signed_cert(publisher=publisher)
        return _apply_signature_with_pfx(exe_path, gen_pfx, gen_pwd)

    if allow_unsigned:
        print("[Sign] 未配置签名证书（本地无 certs/*.pfx 且未设置 CODESIGN_PFX_BASE64），按允许模式跳过签名。")
        return None

    raise RuntimeError(
        "未找到签名证书。请运行 `python scripts/sign_binary.py --init-self-signed` 生成本地 starmiaoa 证书，"
        "或配置环境变量 CODESIGN_PFX_BASE64 / CODESIGN_PFX_PASSWORD。"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Windows Authenticode 代码签名工具（签名提供方: starmiaoa）")
    parser.add_argument("target", nargs="?", type=Path, help="待签名的 EXE/DLL 路径")
    parser.add_argument("--init-self-signed", action="store_true", help="初始化本地 10 年期 starmiaoa 自签名证书")
    parser.add_argument("--auto-init", action="store_true", help="本地无证书时自动初始化 starmiaoa 证书并签名")
    parser.add_argument("--allow-unsigned", action="store_true", help="未配置证书时静默跳过而非报错（适合 CI Fork）")
    parser.add_argument("--inspect", action="store_true", help="仅查看目标文件的现有数字签名信息")
    parser.add_argument("--publisher", type=str, default=DEFAULT_PUBLISHER, help="证书签发者/提供方名称（默认 starmiaoa）")
    args = parser.parse_args(argv)

    if args.init_self_signed:
        ensure_local_self_signed_cert(publisher=args.publisher)
        if not args.target:
            return 0

    if not args.target:
        parser.error("请提供目标 EXE 路径，或使用 --init-self-signed。")

    if args.inspect:
        info = inspect_signature(args.target)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    info = sign_executable(
        args.target,
        auto_init_self_signed=args.auto_init,
        allow_unsigned=args.allow_unsigned,
        publisher=args.publisher,
    )
    if info:
        print(f"[Sign] 签名完成: {args.target.name}")
        print(f"       签名提供方 : {info.get('SignerSubject')}")
        print(f"       证书指纹   : {info.get('SignerThumbprint')}")
        print(f"       有效期至   : {info.get('SignerNotAfter')}")
        print(f"       时间戳服务 : {info.get('TimeStamper') or '(离线无时间戳)'}")
        print(f"       链路状态   : {info.get('Status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
