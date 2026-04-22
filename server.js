const { spawn, execSync } = require("child_process");
const http = require("http");
const https = require("https");
const fs = require("fs");
const path = require("path");

const noCapture = process.argv.includes("--no-capture");
const cameraName = process.argv.find((a) => a.startsWith("--camera="))?.split("=")[1] || "Insta360 X4";

// Find ffmpeg
let ffmpegPath = "";
try {
  ffmpegPath = execSync("where ffmpeg", { encoding: "utf-8" }).trim().split("\n")[0].trim();
  console.log(`[ffmpeg] Found at: ${ffmpegPath}`);
} catch {
  console.error("[ffmpeg] Not found in PATH. Install ffmpeg and add it to PATH.");
  process.exit(1);
}

// Create output directory for HLS segments
const hlsDir = path.join(__dirname, "hls");
if (!fs.existsSync(hlsDir)) fs.mkdirSync(hlsDir);

// HTTP server that serves player.html and HLS segments
const playerPath = path.join(__dirname, "media", "player.html");

const MIME = {
  ".html": "text/html",
  ".m3u8": "application/vnd.apple.mpegurl",
  ".ts": "video/mp2t",
};

function handler(req, res) {
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Cache-Control": "no-cache",
  };

  // Player page
  if (req.url === "/" || req.url === "/player.html") {
    res.writeHead(200, { ...headers, "Content-Type": "text/html" });
    res.end(fs.readFileSync(playerPath));
    return;
  }

  // HLS files (.m3u8, .ts)
  const ext = path.extname(req.url);
  if (MIME[ext]) {
    const filePath = path.join(hlsDir, path.basename(req.url));
    if (fs.existsSync(filePath)) {
      res.writeHead(200, { ...headers, "Content-Type": MIME[ext] });
      res.end(fs.readFileSync(filePath));
    } else {
      res.writeHead(404, headers);
      res.end("Not found - is a stream active?");
    }
    return;
  }

  res.writeHead(404, headers);
  res.end("Not found");
}

// HTTP on port 8080
http.createServer(handler).listen(8080, "0.0.0.0", () => {
  console.log("[server] HTTP:  http://0.0.0.0:8080");
});

// HTTPS on port 8443 (for Quest 360 VR)
try {
  const sslOpts = {
    key: fs.readFileSync(path.join(__dirname, "key.pem")),
    cert: fs.readFileSync(path.join(__dirname, "cert.pem")),
  };
  https.createServer(sslOpts, handler).listen(8443, "0.0.0.0", () => {
    console.log("[server] HTTPS: https://0.0.0.0:8443 (for Quest 360 VR)");
  });
} catch {
  console.warn("[server] No SSL certs found. HTTPS disabled.");
}

// ffmpeg: capture webcam → HLS segments
let ffmpegProc = null;
if (!noCapture) {
  const hlsOutput = path.join(hlsDir, "stream.m3u8");

  console.log(`[capture] Starting webcam capture from "${cameraName}"...`);
  ffmpegProc = spawn(ffmpegPath, [
    "-f", "dshow",
    "-i", `video=${cameraName}`,
    "-c:v", "libx264",
    "-preset", "ultrafast",
    "-tune", "zerolatency",
    "-g", "30",
    "-sc_threshold", "0",
    "-f", "hls",
    "-hls_time", "1",
    "-hls_list_size", "5",
    "-hls_flags", "delete_segments",
    "-hls_segment_filename", path.join(hlsDir, "seg%03d.ts"),
    hlsOutput,
  ], { stdio: ["ignore", "pipe", "pipe"] });

  ffmpegProc.stderr.on("data", (data) => {
    const line = data.toString().trim();
    if (line) console.log(`[capture] ${line}`);
  });

  ffmpegProc.on("close", (code) => {
    console.log(`[capture] ffmpeg exited with code ${code}`);
    ffmpegProc = null;
  });

  ffmpegProc.on("error", (err) => {
    console.error(`[capture] ffmpeg error: ${err.message}`);
  });
} else {
  console.log("[capture] Skipped (--no-capture).");
}

// Cleanup
function cleanup() {
  if (ffmpegProc) {
    console.log("[capture] Stopping ffmpeg...");
    ffmpegProc.kill("SIGTERM");
  }
  // Clean up HLS segments
  try {
    for (const f of fs.readdirSync(hlsDir)) fs.unlinkSync(path.join(hlsDir, f));
  } catch {}
  process.exit();
}
process.on("SIGINT", cleanup);
process.on("SIGTERM", cleanup);

console.log("\n--- Local 360 Stream Server ---");
console.log(`Camera:      ${cameraName}`);
console.log("Player:      http://<YOUR_IP>:8080");
console.log("Player (VR): https://<YOUR_IP>:8443");
console.log("HLS stream:  http://<YOUR_IP>:8080/stream.m3u8");
console.log("-------------------------------\n");
