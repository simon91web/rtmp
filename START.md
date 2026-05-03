# Quick Start

## Every time

1. **Camera** — switch Insta360 X4 to webcam mode, plug in USB
2. **Hotspot** — Windows Settings → Network → Mobile Hotspot → turn on
3. **Server** — open PowerShell in `C:\Users\Simon\Code\rtmp`, run:
   ```
   node server.js
   ```
4. **Phone / Quest** — connect to the PC hotspot (not venue WiFi)

## Open the player

| Device | URL | Mode |
|--------|-----|------|
| Android phone | `http://192.168.137.1:8080` | 360 VR View → Fullscreen |
| Meta Quest | `http://192.168.137.1:8080` | 360 VR View → goggles icon |
| Laptop | `http://192.168.137.1:8080` | any |

## Notes

- Hotspot IP is always `192.168.137.1` (fixed by Windows)
- Don't use venue WiFi — AP isolation blocks device-to-device traffic
- If stream doesn't start, check PowerShell for `[capture]` errors
