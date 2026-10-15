from typing import Optional

import socket


PORT_CONFLICT_ERRNOS = {48, 98, 10048}
# 本机不存在该地址或不支持该地址族（EADDRNOTAVAIL: macOS 49 / Linux 99 / Windows 10049；
# EAFNOSUPPORT: macOS 47 / Linux 97 / Windows 10047），例如系统禁用 IPv6 后绑定 ::1，不属于端口占用
ADDRESS_UNAVAILABLE_ERRNOS = {47, 49, 97, 99, 10047, 10049}


def _detect_socket_family(host: str) -> socket.AddressFamily:
    return socket.AF_INET6 if ":" in host else socket.AF_INET


def _normalize_test_host(host: str) -> str:
    if host == "0.0.0.0":
        return "127.0.0.1"
    return "::1" if host == "::" else host


def is_port_conflict_error(error: OSError) -> bool:
    errno = getattr(error, "errno", None)
    if errno in PORT_CONFLICT_ERRNOS:
        return True

    message = str(error).lower()
    return "address already in use" in message or "已被占用" in message


def is_address_unavailable_error(error: OSError) -> bool:
    """判断绑定错误是否为本机不可用的地址（而非端口占用）。"""
    return error.errno in ADDRESS_UNAVAILABLE_ERRNOS


def check_port_available(host: str, port: int, *, allow_reuse_addr: bool = False) -> bool:
    """检查端口是否可绑定。

    仅端口冲突返回 False；其余绑定错误（如本机不存在该地址）原样抛出，由调用方决定如何处理，
    避免把非占用问题误报为「端口已被占用」。
    """
    family = _detect_socket_family(host)
    test_host = _normalize_test_host(host)

    try:
        with socket.socket(family, socket.SOCK_STREAM) as test_socket:
            test_socket.settimeout(1)
            if allow_reuse_addr:
                test_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_socket.bind((test_host, port))
            return True
    except OSError as error:
        if is_port_conflict_error(error):
            return False
        raise


def build_port_conflict_message(service_name: str, host: str, port: int) -> str:
    return f"{service_name} 启动失败: 端口 {port} 已被占用 (host={host})"


def log_port_conflict(
    logger,
    *,
    service_name: str,
    host: str,
    port: int,
    config_hint: Optional[str] = None,
) -> None:
    logger.error(f"❌ {build_port_conflict_message(service_name=service_name, host=host, port=port)}")
    logger.error(f"💡 请检查是否有其他程序正在使用端口 {port}")
    if config_hint:
        logger.error(f"💡 请修改配置项 {config_hint} 来更改端口")
    logger.error(f"💡 Windows 用户可以运行: netstat -ano | findstr :{port}")
    logger.error(f"💡 Linux/Mac 用户可以运行: lsof -i :{port}")


def assert_port_available(
    *,
    host: str,
    port: int,
    service_name: str,
    logger,
    config_hint: Optional[str] = None,
    allow_reuse_addr: bool = False,
) -> None:
    if check_port_available(host=host, port=port, allow_reuse_addr=allow_reuse_addr):
        return

    log_port_conflict(
        logger,
        service_name=service_name,
        host=host,
        port=port,
        config_hint=config_hint,
    )
    raise OSError(build_port_conflict_message(service_name=service_name, host=host, port=port))
