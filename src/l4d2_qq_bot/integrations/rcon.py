"""Bounded asyncio Source RCON; protocol and Chinese replies adapted from upstream."""

import asyncio
import struct
from contextlib import suppress

from ..address import parse_address


class RCONError(ValueError):
    pass


class RCONClient:
    async def execute(self, address: str, password: str, command: str) -> str:
        endpoint = parse_address(address)
        writer = None
        try:
            async with asyncio.timeout(15):
                reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)

                async def send(identifier: int, kind: int, body: str):
                    raw = body.encode("utf-8")
                    if len(raw) > 4000 or b"\0" in raw:
                        raise RCONError("指令或密码长度超出限制")
                    writer.write(
                        struct.pack("<iii", len(raw) + 10, identifier, kind) + raw + b"\0\0"
                    )
                    await writer.drain()

                async def receive():
                    size = struct.unpack("<i", await reader.readexactly(4))[0]
                    if not 10 <= size <= 4096:
                        raise RCONError("服务器返回了无效的 RCON 数据")
                    raw = await reader.readexactly(size)
                    if raw[-2:] != b"\0\0":
                        raise RCONError("服务器返回了无效的 RCON 数据")
                    return *struct.unpack("<ii", raw[:8]), raw[8:-2]

                await send(1, 3, password)
                for _ in range(4):
                    identifier, kind, _ = await receive()
                    if kind == 2:
                        if identifier == -1:
                            raise RCONError("RCON 认证失败：密码错误")
                        if identifier != 1:
                            raise RCONError("RCON 认证响应不匹配")
                        break
                else:
                    raise RCONError("未收到 RCON 认证响应")
                await send(2, 2, command)
                if command == "_restart":
                    return "指令已发送。服务器正在重启..."
                await send(3, 0, "")
                output = bytearray()
                for _ in range(256):
                    identifier, kind, raw = await receive()
                    if identifier == 3:
                        response = output.decode("utf-8", errors="replace")
                        return (
                            f"服务器响应: {response}"
                            if response
                            else "指令已发送。服务器无文本响应。"
                        )
                    if identifier == 2 and kind == 0:
                        output.extend(raw)
                        if len(output) > 256 * 1024:
                            break
                raise RCONError("服务器响应过长")
        except TimeoutError:
            return "操作超时：连接服务器耗时过长，请检查服务器状态或网络连接。"
        except RCONError as exc:
            return f"RCON 执行出错: {exc}"
        except (OSError, asyncio.IncompleteReadError):
            return "RCON 执行出错: 连接服务器失败或连接已关闭"
        finally:
            if writer is not None:
                writer.close()
                with suppress(OSError, TimeoutError):
                    async with asyncio.timeout(1):
                        await writer.wait_closed()
