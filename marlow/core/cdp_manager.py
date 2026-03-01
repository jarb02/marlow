"""
Marlow CDP Manager — Chrome DevTools Protocol for Electron/CEF Apps

Manages WebSocket connections to apps exposing CDP (Chrome DevTools Protocol).
Enables 100% invisible automation: click, type, screenshot, DOM — all without
stealing focus or requiring the window to be visible.

Discovery: scans localhost ports for CDP endpoints.
Connection: WebSocket to the target page's devtools endpoint.
Commands: Input.dispatch*, Page.captureScreenshot, Runtime.evaluate, DOM.getDocument.

/ Manager de CDP para apps Electron/CEF.
/ Automatizacion 100% invisible via WebSocket.
"""

import asyncio
import json
import logging
import threading
from typing import Optional

import websocket  # websocket-client (sync)

logger = logging.getLogger("marlow.core.cdp_manager")

# ── Singleton ──

_manager: Optional["CDPManager"] = None
_lock = threading.Lock()


def get_manager() -> "CDPManager":
    """Get or create the singleton CDPManager."""
    global _manager
    if _manager is None:
        with _lock:
            if _manager is None:
                _manager = CDPManager()
    return _manager


class CDPManager:
    """
    Manages CDP WebSocket connections to Electron/CEF apps.

    Thread-safe: connections dict protected by lock.
    All public methods are async (blocking ops via run_in_executor).
    """

    def __init__(self):
        self._connections: dict[int, dict] = {}  # port -> {ws, info, msg_id}
        self._lock = threading.Lock()

    # ─────────────────────────────────────────────────────────
    # Discovery
    # ─────────────────────────────────────────────────────────

    async def discover_cdp_ports(
        self, port_range: tuple[int, int] = (9222, 9250)
    ) -> dict:
        """
        Scan localhost ports for active CDP endpoints.

        Tries HTTP GET /json on each port to find debuggable pages.
        Returns list of discovered targets.

        / Escanea puertos localhost buscando endpoints CDP activos.
        """
        loop = asyncio.get_running_loop()
        start, end = port_range

        async def _probe(port: int) -> Optional[dict]:
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, self._probe_port, port),
                    timeout=2.0,
                )
            except (asyncio.TimeoutError, Exception):
                return None

        tasks = [_probe(p) for p in range(start, end + 1)]
        results = await asyncio.gather(*tasks)

        targets = []
        for r in results:
            if r is not None:
                targets.extend(r)

        return {
            "success": True,
            "targets": targets,
            "count": len(targets),
            "ports_scanned": f"{start}-{end}",
        }

    def _probe_port(self, port: int) -> Optional[list[dict]]:
        """Synchronous probe of a single port for CDP /json endpoint."""
        import httpx

        try:
            resp = httpx.get(
                f"http://localhost:{port}/json",
                timeout=1.5,
                follow_redirects=False,
            )
            if resp.status_code != 200:
                return None

            pages = resp.json()
            if not isinstance(pages, list):
                return None

            targets = []
            for page in pages:
                if page.get("type") != "page":
                    continue
                ws_url = page.get("webSocketDebuggerUrl", "")
                if not ws_url:
                    continue
                targets.append({
                    "port": port,
                    "title": page.get("title", ""),
                    "url": page.get("url", ""),
                    "websocket_url": ws_url,
                    "id": page.get("id", ""),
                })

            return targets if targets else None

        except Exception:
            return None

    # ─────────────────────────────────────────────────────────
    # Connection management
    # ─────────────────────────────────────────────────────────

    async def connect(self, port: int) -> dict:
        """
        Connect to a CDP endpoint on the given port.

        Discovers the first page target and establishes WebSocket.
        If already connected to this port, returns existing info.

        / Conecta a un endpoint CDP en el puerto dado.
        """
        with self._lock:
            if port in self._connections:
                info = self._connections[port]["info"]
                return {
                    "success": True,
                    "already_connected": True,
                    "port": port,
                    **info,
                }

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, self._connect_sync, port)
        return result

    def _connect_sync(self, port: int) -> dict:
        """Synchronous connect: probe port, open WebSocket."""
        import httpx

        # Discover target
        try:
            resp = httpx.get(
                f"http://localhost:{port}/json",
                timeout=3.0,
                follow_redirects=False,
            )
            if resp.status_code != 200:
                return {"error": f"Port {port} returned HTTP {resp.status_code}"}

            pages = resp.json()
        except Exception as e:
            return {"error": f"Cannot reach CDP on port {port}: {e}"}

        # Find first page target
        target = None
        for page in pages:
            if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                target = page
                break

        if not target:
            return {
                "error": f"No debuggable page found on port {port}",
                "hint": "Make sure the app was launched with --remote-debugging-port.",
            }

        ws_url = target["webSocketDebuggerUrl"]
        info = {
            "title": target.get("title", ""),
            "url": target.get("url", ""),
            "websocket_url": ws_url,
            "target_id": target.get("id", ""),
        }

        # Open WebSocket
        try:
            ws = websocket.create_connection(ws_url, timeout=5)
        except Exception as e:
            return {"error": f"WebSocket connection failed: {e}"}

        with self._lock:
            self._connections[port] = {
                "ws": ws,
                "info": info,
                "msg_id": 0,
            }

        logger.info(f"CDP connected to port {port}: {info['title']}")
        return {
            "success": True,
            "port": port,
            **info,
        }

    async def disconnect(self, port: int) -> dict:
        """
        Close CDP connection on the given port.

        / Cierra la conexion CDP en el puerto dado.
        """
        with self._lock:
            conn = self._connections.pop(port, None)

        if not conn:
            return {"error": f"No active connection on port {port}"}

        try:
            conn["ws"].close()
        except Exception:
            pass

        logger.info(f"CDP disconnected from port {port}")
        return {"success": True, "port": port, "disconnected": True}

    async def list_connections(self) -> dict:
        """
        List all active CDP connections.

        / Lista todas las conexiones CDP activas.
        """
        with self._lock:
            conns = []
            for port, conn in self._connections.items():
                conns.append({
                    "port": port,
                    **conn["info"],
                })

        return {
            "success": True,
            "connections": conns,
            "count": len(conns),
        }

    # ─────────────────────────────────────────────────────────
    # Command execution
    # ─────────────────────────────────────────────────────────

    async def send_command(
        self, port: int, method: str, params: Optional[dict] = None
    ) -> dict:
        """
        Send a CDP command and wait for response.

        Args:
            port: CDP port to send to.
            method: CDP method (e.g., "Page.captureScreenshot").
            params: Optional parameters dict.

        Returns:
            CDP response result or error.

        / Envia un comando CDP y espera respuesta. Timeout 10s.
        """
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(
                None, self._send_command_sync, port, method, params
            ),
            timeout=10.0,
        )

    def _send_command_sync(
        self, port: int, method: str, params: Optional[dict]
    ) -> dict:
        """Synchronous send + receive on WebSocket."""
        with self._lock:
            conn = self._connections.get(port)
            if not conn:
                return {"error": f"No active connection on port {port}"}
            conn["msg_id"] += 1
            msg_id = conn["msg_id"]
            ws = conn["ws"]

        msg = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params

        try:
            ws.send(json.dumps(msg))
        except Exception as e:
            self._cleanup_connection(port)
            return {"error": f"Send failed (connection lost): {e}"}

        # Wait for matching response (skip events)
        try:
            ws.settimeout(10.0)
            while True:
                raw = ws.recv()
                resp = json.loads(raw)
                if resp.get("id") == msg_id:
                    if "error" in resp:
                        return {
                            "error": f"CDP error: {resp['error'].get('message', resp['error'])}",
                            "code": resp["error"].get("code"),
                        }
                    return {"success": True, "result": resp.get("result", {})}
                # Skip events (no "id" field) — keep reading
        except websocket.WebSocketTimeoutException:
            return {"error": f"CDP response timeout (10s) for {method}"}
        except Exception as e:
            self._cleanup_connection(port)
            return {"error": f"Recv failed (connection lost): {e}"}

    def _cleanup_connection(self, port: int) -> None:
        """Remove a dead connection from the dict."""
        with self._lock:
            conn = self._connections.pop(port, None)
        if conn:
            try:
                conn["ws"].close()
            except Exception:
                pass
            logger.warning(f"CDP connection on port {port} lost, cleaned up")

    # ─────────────────────────────────────────────────────────
    # Input functions (100% invisible, no focus required)
    # ─────────────────────────────────────────────────────────

    async def cdp_click(self, port: int, x: int, y: int) -> dict:
        """
        Click at (x, y) via CDP Input.dispatchMouseEvent.

        Sends mousePressed + mouseReleased. Coordinates are relative
        to the page viewport, not the screen.

        / Click en (x, y) via CDP — invisible, sin robar foco.
        """
        press = await self.send_command(port, "Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
        })
        if "error" in press:
            return press

        release = await self.send_command(port, "Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1,
        })
        if "error" in release:
            return release

        return {"success": True, "x": x, "y": y, "action": "click"}

    async def cdp_type(self, port: int, text: str) -> dict:
        """
        Type text via CDP Input.insertText.

        For plain text, uses insertText (handles unicode correctly).

        / Escribe texto via CDP — invisible, sin robar foco.
        """
        result = await self.send_command(port, "Input.insertText", {
            "text": text,
        })
        if "error" in result:
            return result

        return {"success": True, "text": text, "length": len(text)}

    async def cdp_key_combo(
        self, port: int, key: str, modifiers: list[str] | None = None
    ) -> dict:
        """
        Press a key combination via CDP Input.dispatchKeyEvent.

        Args:
            port: CDP port.
            key: Key name (e.g., "a", "Enter", "Tab", "Escape").
            modifiers: List of modifier names: "ctrl", "alt", "shift", "meta".

        / Combinacion de teclas via CDP — invisible.
        """
        # Build modifier bitmask
        mod_map = {"alt": 1, "ctrl": 2, "meta": 4, "shift": 8}
        mod_bits = 0
        for m in (modifiers or []):
            mod_bits |= mod_map.get(m.lower(), 0)

        # Key mapping for special keys
        _KEYS = {
            "enter": ("Enter", "\r", 13),
            "tab": ("Tab", "", 9),
            "escape": ("Escape", "", 27),
            "backspace": ("Backspace", "", 8),
            "delete": ("Delete", "", 46),
            "arrowup": ("ArrowUp", "", 38),
            "arrowdown": ("ArrowDown", "", 40),
            "arrowleft": ("ArrowLeft", "", 37),
            "arrowright": ("ArrowRight", "", 39),
            "home": ("Home", "", 36),
            "end": ("End", "", 35),
            "pageup": ("PageUp", "", 33),
            "pagedown": ("PageDown", "", 34),
        }

        key_lower = key.lower()
        if key_lower in _KEYS:
            key_id, text_val, code = _KEYS[key_lower]
        else:
            key_id = key
            text_val = key if len(key) == 1 else ""
            code = ord(key.upper()) if len(key) == 1 else 0

        base_params = {
            "key": key_id,
            "modifiers": mod_bits,
            "windowsVirtualKeyCode": code,
        }
        if text_val:
            base_params["text"] = text_val

        # keyDown
        down = await self.send_command(port, "Input.dispatchKeyEvent", {
            "type": "keyDown", **base_params,
        })
        if "error" in down:
            return down

        # keyUp
        up = await self.send_command(port, "Input.dispatchKeyEvent", {
            "type": "keyUp", **base_params,
        })
        if "error" in up:
            return up

        return {
            "success": True,
            "key": key,
            "modifiers": modifiers or [],
        }

    # ─────────────────────────────────────────────────────────
    # Reading functions
    # ─────────────────────────────────────────────────────────

    async def cdp_screenshot(
        self, port: int, format: str = "png"
    ) -> dict:
        """
        Take a screenshot via CDP Page.captureScreenshot.

        Returns base64-encoded image. This works even if the window
        is behind other windows or minimized.

        / Screenshot via CDP — funciona aunque la ventana este detras u oculta.
        """
        fmt = format.lower()
        if fmt not in ("png", "jpeg"):
            fmt = "png"

        result = await self.send_command(port, "Page.captureScreenshot", {
            "format": fmt,
        })
        if "error" in result:
            return result

        data = result.get("result", {}).get("data")
        if not data:
            return {"error": "Screenshot returned no data"}

        return {
            "success": True,
            "image_base64": data,
            "format": fmt,
        }

    async def cdp_evaluate(self, port: int, expression: str) -> dict:
        """
        Evaluate JavaScript expression via CDP Runtime.evaluate.

        Args:
            port: CDP port.
            expression: JS expression to evaluate.

        Returns:
            The evaluation result (value, type).

        / Evalua expresion JavaScript via CDP.
        """
        result = await self.send_command(port, "Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
        })
        if "error" in result:
            return result

        eval_result = result.get("result", {}).get("result", {})
        exception = result.get("result", {}).get("exceptionDetails")

        if exception:
            return {
                "error": f"JS error: {exception.get('text', str(exception))}",
                "exception": exception,
            }

        return {
            "success": True,
            "value": eval_result.get("value"),
            "type": eval_result.get("type", "undefined"),
        }

    async def cdp_get_dom(self, port: int, depth: int = -1) -> dict:
        """
        Get DOM tree via CDP DOM.getDocument.

        Args:
            port: CDP port.
            depth: Tree depth (-1 = full tree).

        Returns:
            DOM root node.

        / Obtiene arbol DOM via CDP.
        """
        result = await self.send_command(port, "DOM.getDocument", {
            "depth": depth,
        })
        if "error" in result:
            return result

        root = result.get("result", {}).get("root")
        if not root:
            return {"error": "DOM.getDocument returned no root"}

        return {
            "success": True,
            "root": root,
            "node_count": self._count_nodes(root),
        }

    def _count_nodes(self, node: dict) -> int:
        """Count nodes in a DOM tree recursively."""
        count = 1
        for child in node.get("children", []):
            count += self._count_nodes(child)
        return count

    # ─────────────────────────────────────────────────────────
    # Convenience functions
    # ─────────────────────────────────────────────────────────

    async def cdp_click_selector(self, port: int, css_selector: str) -> dict:
        """
        Click an element by CSS selector via Runtime.evaluate.

        Executes document.querySelector(selector).click() in the page context.

        / Click en elemento por selector CSS via JavaScript.
        """
        # Escape the selector for JS string
        safe_sel = css_selector.replace("\\", "\\\\").replace("'", "\\'")
        js = (
            f"(() => {{"
            f"  const el = document.querySelector('{safe_sel}');"
            f"  if (!el) return {{error: 'Element not found: {safe_sel}'}};"
            f"  el.click();"
            f"  return {{clicked: true, tag: el.tagName, text: (el.textContent || '').slice(0, 100)}};"
            f"}})()"
        )

        result = await self.cdp_evaluate(port, js)
        if "error" in result:
            return result

        value = result.get("value")
        if isinstance(value, dict) and value.get("error"):
            return {"error": value["error"]}

        return {
            "success": True,
            "selector": css_selector,
            **(value if isinstance(value, dict) else {}),
        }


# ─────────────────────────────────────────────────────────────
# MCP Tool Functions (async, called from server.py dispatch)
# ─────────────────────────────────────────────────────────────

async def cdp_discover(
    port_start: int = 9222,
    port_end: int = 9250,
) -> dict:
    """
    Discover apps with CDP enabled on localhost.

    Scans port range for active CDP endpoints.
    Returns list of targets with port, title, URL, and WebSocket URL.

    / Descubre apps con CDP habilitado en localhost.
    """
    mgr = get_manager()
    return await mgr.discover_cdp_ports((port_start, port_end))


async def cdp_connect(port: int) -> dict:
    """
    Connect to a CDP endpoint.

    / Conecta a un endpoint CDP.
    """
    mgr = get_manager()
    return await mgr.connect(port)


async def cdp_disconnect(port: int) -> dict:
    """
    Disconnect from a CDP endpoint.

    / Desconecta de un endpoint CDP.
    """
    mgr = get_manager()
    return await mgr.disconnect(port)


async def cdp_list_connections() -> dict:
    """
    List all active CDP connections.

    / Lista todas las conexiones CDP activas.
    """
    mgr = get_manager()
    return await mgr.list_connections()


async def cdp_send(
    port: int,
    method: str,
    params: Optional[dict] = None,
) -> dict:
    """
    Send a raw CDP command.

    / Envia un comando CDP crudo.
    """
    mgr = get_manager()
    return await mgr.send_command(port, method, params)


async def cdp_click(port: int, x: int, y: int) -> dict:
    """
    Click at page coordinates via CDP (invisible, no focus needed).

    / Click en coordenadas de pagina via CDP (invisible).
    """
    mgr = get_manager()
    return await mgr.cdp_click(port, x, y)


async def cdp_type_text(port: int, text: str) -> dict:
    """
    Type text via CDP (invisible, no focus needed).

    / Escribe texto via CDP (invisible).
    """
    mgr = get_manager()
    return await mgr.cdp_type(port, text)


async def cdp_key_combo(
    port: int,
    key: str,
    modifiers: list[str] | None = None,
) -> dict:
    """
    Press key combination via CDP (invisible).

    / Combinacion de teclas via CDP (invisible).
    """
    mgr = get_manager()
    return await mgr.cdp_key_combo(port, key, modifiers)


async def cdp_screenshot(port: int, format: str = "png") -> dict:
    """
    Take screenshot via CDP (works even if window is hidden).

    / Screenshot via CDP (funciona aunque ventana este oculta).
    """
    mgr = get_manager()
    return await mgr.cdp_screenshot(port, format)


async def cdp_evaluate(port: int, expression: str) -> dict:
    """
    Evaluate JavaScript in the page context via CDP.

    / Evalua JavaScript en el contexto de la pagina via CDP.
    """
    mgr = get_manager()
    return await mgr.cdp_evaluate(port, expression)


async def cdp_get_dom(port: int, depth: int = -1) -> dict:
    """
    Get the DOM tree via CDP.

    / Obtiene el arbol DOM via CDP.
    """
    mgr = get_manager()
    return await mgr.cdp_get_dom(port, depth)


async def cdp_click_selector(port: int, css_selector: str) -> dict:
    """
    Click element by CSS selector via CDP (invisible).

    / Click en elemento por selector CSS via CDP (invisible).
    """
    mgr = get_manager()
    return await mgr.cdp_click_selector(port, css_selector)
