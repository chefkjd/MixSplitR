"""
splitter_ui.py - Visual audio splitter with waveform UI
A module for MixSplitR that provides browser-based split point selection

Usage:
    from splitter_ui import get_split_points_visual
    
    # Returns list of split points in seconds, or None if cancelled
    split_points = get_split_points_visual("/path/to/mix.wav")
"""

import os
import sys
import json
import webbrowser
import threading
import tempfile
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse
from urllib.request import urlopen

# Check for pydub (should already be available via MixSplitR)
try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False

# Module state
_server = None
_server_thread = None
_temp_dir = None
_audio_file = None
_audio_duration = 0
_split_points = []
_user_done = False
_user_cancelled = False

PORT = 8765


def _runtime_waveform_temp_base():
    """Return a stable temp root for browser waveform editor artifacts."""
    try:
        from mixsplitr_core import get_runtime_temp_directory
        temp_base = str(get_runtime_temp_directory("waveform_ui"))
    except Exception:
        temp_base = os.path.join(tempfile.gettempdir(), "MixSplitR", "waveform_ui")
    try:
        os.makedirs(temp_base, exist_ok=True)
    except Exception:
        pass
    return temp_base


def _convert_to_wav(input_file, output_file):
    """Convert any audio format to WAV for web playback"""
    audio = AudioSegment.from_file(input_file)
    # Convert to mono and reduce sample rate for faster loading
    audio = audio.set_channels(1).set_frame_rate(22050)
    audio.export(output_file, format="wav")
    return len(audio) / 1000  # Return duration in seconds


class _SplitterHandler(SimpleHTTPRequestHandler):
    """HTTP handler for the visual splitter"""
    
    def log_message(self, format, *args):
        """Suppress logging"""
        pass
    
    def do_GET(self):
        global _audio_duration
        parsed = urlparse(self.path)
        
        if parsed.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(_get_html_template().encode())
            
        elif parsed.path == '/audio.wav':
            wav_path = os.path.join(_temp_dir, 'audio.wav')
            if os.path.exists(wav_path):
                self.send_response(200)
                self.send_header('Content-type', 'audio/wav')
                self.send_header('Accept-Ranges', 'bytes')
                file_size = os.path.getsize(wav_path)
                self.send_header('Content-Length', str(file_size))
                self.end_headers()
                with open(wav_path, 'rb') as f:
                    shutil.copyfileobj(f, self.wfile)
            else:
                self.send_error(404)
                
        elif parsed.path == '/info':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            info = {
                'filename': os.path.basename(_audio_file),
                'duration': _audio_duration,
                'existing_points': _split_points  # Pre-populated points for assisted mode
            }
            self.wfile.write(json.dumps(info).encode())
        else:
            self.send_error(404)
    
    def do_POST(self):
        global _split_points, _user_done, _user_cancelled
        parsed = urlparse(self.path)
        
        if parsed.path == '/done':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode())
            
            _split_points = sorted(data.get('split_points', []))
            _user_done = True
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
            
        elif parsed.path == '/cancel':
            _user_cancelled = True
            _user_done = True
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'success': True}).encode())
        else:
            self.send_error(404)


def _run_server():
    """Run the HTTP server"""
    global _server
    _server = HTTPServer(('127.0.0.1', PORT), _SplitterHandler)
    _server.serve_forever()


def _stop_server():
    """Stop the HTTP server"""
    global _server
    if _server:
        _server.shutdown()
        _server = None


def get_split_points_visual(audio_file, existing_points=None, open_browser=True, on_server_ready=None):
    """
    Open a browser-based visual editor for selecting split points.
    
    Args:
        audio_file: Path to the audio file
        existing_points: Optional list of existing split points to edit
        open_browser: If True, launch external browser automatically
        on_server_ready: Optional callback receiving editor URL when local server is ready
        
    Returns:
        List of split points in seconds, or None if cancelled
    """
    global _temp_dir, _audio_file, _audio_duration, _split_points
    global _user_done, _user_cancelled, _server_thread
    
    if not PYDUB_AVAILABLE:
        print("  ⚠️  Visual splitter requires pydub")
        return None
    
    # Reset state
    _user_done = False
    _user_cancelled = False
    _split_points = existing_points or []
    _audio_file = audio_file
    
    # Create temp directory
    temp_base = _runtime_waveform_temp_base()
    try:
        _temp_dir = tempfile.mkdtemp(prefix="mixsplitr_ui_", dir=temp_base)
    except Exception:
        _temp_dir = tempfile.mkdtemp(prefix="mixsplitr_ui_")
    
    try:
        # Convert audio to WAV for web playback
        print("  📊 Preparing waveform preview...", end='', flush=True)
        wav_path = os.path.join(_temp_dir, 'audio.wav')
        _audio_duration = _convert_to_wav(audio_file, wav_path)
        print(" done")
        
        # Start server
        _server_thread = threading.Thread(target=_run_server, daemon=True)
        _server_thread.start()
        
        url = f"http://localhost:{PORT}"
        info_url = f"{url}/info"

        # Wait until server is actually reachable before continuing.
        server_ready = False
        for _ in range(40):  # up to ~8 seconds
            try:
                with urlopen(info_url, timeout=0.5) as response:
                    if getattr(response, "status", 0) == 200:
                        server_ready = True
                        break
            except Exception:
                time.sleep(0.2)

        if not server_ready:
            raise RuntimeError("Local waveform server did not start in time")

        # Open browser / notify embedded host
        print(f"  🌐 Opening visual editor at {url}")
        print(f"     Set your split points, then click 'Done & Continue'")
        print(f"     Waiting for input...", end='', flush=True)
        
        if callable(on_server_ready):
            try:
                on_server_ready(url)
            except Exception:
                pass
        if open_browser:
            webbrowser.open(url)
        
        # Wait for user to finish
        while not _user_done:
            time.sleep(0.1)
        
        print(" ✓")
        
        if _user_cancelled:
            print("  ℹ️  Cancelled - using automatic splitting")
            return None
        
        if _split_points:
            print(f"  ✓ {len(_split_points)} split point(s) selected")
        else:
            print("  ℹ️  No split points set - using automatic splitting")
            return None
            
        return _split_points
        
    finally:
        # Cleanup
        _stop_server()
        try:
            import shutil
            shutil.rmtree(_temp_dir, ignore_errors=True)
        except OSError:
            pass


def split_audio_at_points(audio_file, split_points):
    """
    Split audio at specified points and return AudioSegment chunks.
    
    Args:
        audio_file: Path to the audio file
        split_points: List of split times in seconds
        
    Returns:
        List of AudioSegment chunks
    """
    audio = AudioSegment.from_file(audio_file)
    total_duration = len(audio) / 1000
    
    # Add start and end
    points = [0] + sorted(split_points) + [total_duration]
    
    chunks = []
    for i in range(len(points) - 1):
        start_ms = int(points[i] * 1000)
        end_ms = int(points[i + 1] * 1000)
        
        # Skip very short segments
        if end_ms - start_ms < 5000:  # Less than 5 seconds
            continue
        
        chunk = audio[start_ms:end_ms]
        chunks.append(chunk)
    
    return chunks


# Import shutil at module level for the handler
import shutil


def _get_html_template():
    """Return the HTML template for the visual editor"""
    return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MixSplitR - Visual Splitter</title>
    <script src="https://unpkg.com/wavesurfer.js@7"></script>
    <script src="https://unpkg.com/wavesurfer.js@7/dist/plugins/regions.min.js"></script>
    <script src="https://unpkg.com/wavesurfer.js@7/dist/plugins/timeline.min.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial;
            background: linear-gradient(135deg, #14182a 0%, #1f2440 100%);
            color: #dce0f2;
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
        }
        header {
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 20px;
        }
        .logo {
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(90deg, #7f8ce6, #5f6fc9);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .filename {
            background: #23283d;
            padding: 8px 16px;
            border-radius: 20px;
            font-family: monospace;
            font-size: 14px;
            color: #7f8ce6;
        }
        .instructions {
            background: #23283d;
            padding: 15px 20px;
            border-radius: 10px;
            margin-bottom: 20px;
            border-left: 4px solid #7f8ce6;
        }
        .instructions h3 {
            color: #7f8ce6;
            margin-bottom: 8px;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .instructions p {
            color: #9aa0bb;
            font-size: 14px;
            line-height: 1.6;
        }
        .instructions kbd {
            background: #14182a;
            padding: 2px 8px;
            border-radius: 4px;
            font-family: monospace;
            color: #dce0f2;
        }
        #waveform-container {
            background: #0f0f1a;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }
        #waveform {
            cursor: crosshair;
        }
        /* Wider split point handles for easier grabbing */
        div[data-id^="split-"] {
            cursor: ew-resize !important;
        }
        div[data-id^="split-"]::before {
            content: '';
            position: absolute;
            top: 0;
            left: -8px;
            right: -8px;
            bottom: 0;
            cursor: ew-resize;
        }
        #timeline {
            display: none;
        }
        /* Custom permanent scrollbar */
        #custom-scrollbar {
            width: 100%;
            height: 16px;
            background: #23283d;
            border-radius: 8px;
            margin-top: 12px;
            position: relative;
            cursor: pointer;
            opacity: 1;
            transition: opacity 0.3s;
        }
        #custom-scrollbar.disabled {
            opacity: 0.3;
            cursor: not-allowed;
        }
        #scrollbar-thumb {
            height: 100%;
            background: #7f8ce6;
            border-radius: 8px;
            position: absolute;
            left: 0;
            width: 100%;
            min-width: 30px;
            cursor: grab;
            transition: background 0.2s;
        }
        #custom-scrollbar:not(.disabled) #scrollbar-thumb:hover {
            background: #9ca7f0;
        }
        #custom-scrollbar:not(.disabled) #scrollbar-thumb:active {
            cursor: grabbing;
            background: #3dbdb5;
        }
        .controls {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            align-items: center;
            margin-bottom: 20px;
        }
        button {
            background: #7f8ce6;
            color: #14182a;
            border: none;
            padding: 12px 28px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 15px;
            font-weight: 600;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        button:hover {
            background: #9ca7f0;
            transform: translateY(-2px);
            box-shadow: 0 4px 15px rgba(78, 205, 196, 0.3);
        }
        button:active { transform: translateY(0); }
        button.secondary {
            background: #23283d;
            color: #dce0f2;
        }
        button.secondary:hover {
            background: #3d3d54;
            box-shadow: 0 4px 15px rgba(0, 0, 0, 0.3);
        }
        button.danger {
            background: #e74c3c;
            color: #dce0f2;
        }
        button.danger:hover {
            background: #c0392b;
        }
        button.done {
            background: linear-gradient(90deg, #7f8ce6, #5f6fc9);
            padding: 14px 32px;
            font-size: 16px;
        }
        .spacer { flex: 1; }
        .time-display {
            font-family: monospace;
            font-size: 18px;
            color: #7f8ce6;
            background: #14182a;
            padding: 10px 20px;
            border-radius: 8px;
            min-width: 100px;
            text-align: center;
        }
        .split-list {
            background: #23283d;
            border-radius: 10px;
            padding: 20px;
        }
        .split-list h3 {
            color: #7f8ce6;
            margin-bottom: 15px;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .split-item {
            display: flex;
            align-items: center;
            gap: 15px;
            padding: 10px 15px;
            background: #14182a;
            border-radius: 8px;
            margin-bottom: 8px;
        }
        .split-item:last-child { margin-bottom: 0; }
        .split-num {
            background: #7f8ce6;
            color: #14182a;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 13px;
        }
        .split-time {
            font-family: monospace;
            font-size: 16px;
            flex: 1;
        }
        .split-duration {
            color: #7b819b;
            font-size: 13px;
        }
        .split-delete {
            background: transparent;
            color: #e74c3c;
            padding: 5px 10px;
            font-size: 13px;
        }
        .split-delete:hover {
            background: #e74c3c;
            color: #dce0f2;
        }
        .no-splits {
            color: #61677f;
            text-align: center;
            padding: 30px;
            font-style: italic;
        }
        .loading {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 200px;
            color: #7f8ce6;
            font-size: 18px;
        }
        .loading::after {
            content: '';
            width: 24px;
            height: 24px;
            border: 3px solid #7f8ce6;
            border-top-color: transparent;
            border-radius: 50%;
            margin-left: 15px;
            animation: spin 1s linear infinite;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .zoom-controls {
            display: flex;
            align-items: center;
            gap: 8px;
            background: #14182a;
            padding: 6px 12px;
            border-radius: 8px;
        }
        .zoom-controls span {
            font-size: 13px;
            color: #7b819b;
        }
        .zoom-btn {
            background: #23283d;
            padding: 6px 12px;
            font-size: 16px;
            min-width: auto;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">MixSplitR</div>
            <div class="filename" id="filename">Loading...</div>
        </header>
        
        <div class="instructions">
            <h3>How to Use</h3>
            <p>
                <strong>Click</strong> on the waveform to seek. <strong>Double-click</strong> to add a split point. 
                <strong>Drag</strong> the markers to adjust. <strong>Ctrl+click</strong> (or <strong>Cmd+click</strong> on Mac) a marker to remove it.
                <strong>Scroll wheel</strong> to zoom in/out. Press <kbd>Space</kbd> to play/pause.
                When finished, click <strong>Done & Continue</strong> to return to MixSplitR.
            </p>
            <div id="assisted-notice" style="display:none; margin-top: 10px; padding: 10px; background: #2a3048; border-radius: 6px; border-left: 3px solid #7f8ce6;">
                <strong>🎯 Assisted Mode:</strong> <span id="preloaded-count">0</span> split points were auto-detected. Review and adjust as needed.
            </div>
        </div>
        
        <div id="waveform-container">
            <div id="waveform"><div class="loading">Loading waveform</div></div>
            <div id="timeline"></div>
            <!-- Custom permanent scrollbar -->
            <div id="custom-scrollbar">
                <div id="scrollbar-thumb"></div>
            </div>
        </div>
        
        <div class="controls">
            <button id="playPause" class="secondary">
                <span id="playIcon">▶</span> Play
            </button>
            <button id="stop" class="secondary">■ Stop</button>
            <div class="time-display">
                <span id="currentTime">0:00</span> / <span id="duration">0:00</span>
            </div>
            <div class="zoom-controls">
                <span>Zoom:</span>
                <button class="zoom-btn" id="zoomOut">−</button>
                <button class="zoom-btn" id="zoomIn">+</button>
            </div>
            <div class="spacer"></div>
            <button id="importTracklist" class="secondary">📋 Import Tracklist</button>
            <button id="clearAll" class="danger">Clear All</button>
            <button id="cancel" class="secondary">Cancel</button>
            <button id="done" class="done">Done & Continue →</button>
        </div>

        <!-- Tracklist Import Modal -->
        <div id="tracklistModal" style="display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.8); z-index:1000; justify-content:center; align-items:center;">
            <div style="background:#23283d; border-radius:12px; padding:30px; max-width:600px; width:90%;">
                <h2 style="color:#7f8ce6; margin-bottom:15px;">Import Tracklist</h2>
                <p style="color:#9aa0bb; margin-bottom:15px; font-size:14px;">
                    Paste your tracklist below. Supported formats:<br>
                    • Simple: <code style="background:#14182a; padding:2px 6px; border-radius:3px;">00:00 Artist - Title</code><br>
                    • Detailed: <code style="background:#14182a; padding:2px 6px; border-radius:3px;">00:00:00 Artist - Title (Album)</code><br>
                    • CUE sheet format
                </p>
                <textarea id="tracklistInput"
                    placeholder="00:00 Artist 1 - Track 1&#10;03:45 Artist 2 - Track 2&#10;07:20 Artist 3 - Track 3"
                    style="width:100%; height:200px; background:#14182a; color:#dce0f2; border:2px solid #7f8ce6; border-radius:8px; padding:15px; font-family:monospace; font-size:13px; resize:vertical; margin-bottom:15px;"></textarea>
                <div style="display:flex; gap:10px; justify-content:flex-end;">
                    <button id="tracklistCancel" class="secondary">Cancel</button>
                    <button id="tracklistApply" style="background:#7f8ce6; color:#14182a; padding:12px 24px; border:none; border-radius:8px; cursor:pointer; font-weight:600;">Apply</button>
                </div>
            </div>
        </div>
        
        <div class="split-list">
            <h3>Split Points (<span id="splitCount">0</span>)</h3>
            <div id="splitItems">
                <div class="no-splits">Double-click on the waveform to add split points</div>
            </div>
        </div>
    </div>
    
    <script>
        let wavesurfer = null;
        let regions = null;
        let splitPoints = [];
        let duration = 0;
        let isDraggingMarker = false;  // Track if we're dragging a split marker
        let zoom = 0;  // Current zoom (pxPerSec); 0 = auto-fit (set on ready)
        let minZoom = 0;  // Minimum zoom to fit entire waveform
        let _scrollContainer = null;  // Real scroll container (set by onZoomOrScroll)
        let _timelinePlugin = null;

        function rebuildTimeline() {
            if (!wavesurfer) return;
            if (_timelinePlugin) { _timelinePlugin.destroy(); _timelinePlugin = null; }
            const pps = zoom || 1;
            // Aim for ~60px between ticks so labels don't overlap
            const raw = 60 / pps;
            const nice = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
            let tick = 600;
            for (const v of nice) { if (v >= raw) { tick = v; break; } }
            // Primary labels every 2 ticks
            const primary = tick * 2;
            _timelinePlugin = wavesurfer.registerPlugin(
                WaveSurfer.Timeline.create({
                    primaryColor: '#7f8ce6',
                    secondaryColor: '#61677f',
                    primaryFontColor: '#7f8ce6',
                    secondaryFontColor: '#61677f',
                    timeInterval: tick,
                    primaryLabelInterval: primary,
                    style: { fontSize: '12px' }
                })
            );
        }
        
        function formatTime(seconds) {
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            return `${m}:${s.toString().padStart(2, '0')}`;
        }
        
        function formatTimeFull(seconds) {
            const m = Math.floor(seconds / 60);
            const s = Math.floor(seconds % 60);
            const ms = Math.floor((seconds % 1) * 10);
            return `${m}:${s.toString().padStart(2, '0')}.${ms}`;
        }
        
        function updateSplitList() {
            const container = document.getElementById('splitItems');
            document.getElementById('splitCount').textContent = splitPoints.length;
            
            if (splitPoints.length === 0) {
                container.innerHTML = '<div class="no-splits">Double-click on the waveform to add split points</div>';
                return;
            }
            
            // Sort split points
            splitPoints.sort((a, b) => a - b);
            
            // Calculate segments
            const segments = [0, ...splitPoints, duration];
            
            let html = '';
            for (let i = 0; i < splitPoints.length; i++) {
                const point = splitPoints[i];
                const segmentDuration = segments[i + 1] - segments[i];
                
                html += `
                    <div class="split-item">
                        <div class="split-num">${i + 1}</div>
                        <div class="split-time">${formatTimeFull(point)}</div>
                        <div class="split-duration">Track ${i + 1}: ${formatTime(segmentDuration)}</div>
                        <button class="split-delete" onclick="deleteSplit(${i})">✕ Remove</button>
                    </div>
                `;
            }
            
            // Add final segment info
            const lastDuration = duration - splitPoints[splitPoints.length - 1];
            html += `
                <div class="split-item" style="opacity: 0.6;">
                    <div class="split-num">${splitPoints.length + 1}</div>
                    <div class="split-time">→ End</div>
                    <div class="split-duration">Track ${splitPoints.length + 1}: ${formatTime(lastDuration)}</div>
                    <div style="width: 80px;"></div>
                </div>
            `;
            
            container.innerHTML = html;
        }
        
        function addSplitPoint(time) {
            // Don't add if too close to existing point or edges
            if (time < 5 || time > duration - 5) return;
            
            for (const point of splitPoints) {
                if (Math.abs(point - time) < 3) return; // Too close
            }
            
            splitPoints.push(time);
            updateRegions();
            updateSplitList();
        }
        
        function deleteSplit(index) {
            splitPoints.splice(index, 1);
            updateRegions();
            updateSplitList();
        }
        
        function updateRegions() {
            // Clear existing regions
            regions.clearRegions();
            
            // Add markers for each split point with wider visual representation
            for (let i = 0; i < splitPoints.length; i++) {
                const region = regions.addRegion({
                    start: splitPoints[i],
                    end: splitPoints[i],
                    color: '#7f8ce6',
                    drag: true,
                    resize: false,
                    id: `split-${i}`
                });
                
                // Make the split line wider after region is added
                requestAnimationFrame(() => {
                    const regionEl = document.querySelector(`div[data-id="split-${i}"]`);
                    if (regionEl) {
                        regionEl.style.width = '3px';
                        regionEl.style.marginLeft = '-1.5px';
                    }
                });
            }
        }
        
        function zoomAtPoint(newZoom, clientX) {
            if (newZoom === zoom) return;

            const waveformEl = document.getElementById('waveform');
            const rect = waveformEl.getBoundingClientRect();
            const mouseX = clientX ? (clientX - rect.left) : (rect.width / 2);

            // Use the real scroll container (discovered by onZoomOrScroll), not the wrapper
            const sc = _scrollContainer;
            let timeAtPoint = duration / 2; // fallback: center
            if (sc && sc.scrollWidth > sc.clientWidth + 1) {
                const pixelPosition = sc.scrollLeft + mouseX;
                timeAtPoint = (pixelPosition / sc.scrollWidth) * duration;
            }

            zoom = newZoom;
            wavesurfer.zoom(zoom);

            requestAnimationFrame(() => {
                // Re-discover scroll container after zoom since it may change
                const wrapper = wavesurfer.getWrapper ? wavesurfer.getWrapper() : null;
                let el = wrapper || waveformEl;
                let newSc = null;
                while (el && el !== document.documentElement) {
                    if (el.scrollWidth > el.clientWidth + 1) { newSc = el; break; }
                    el = el.parentElement;
                }
                _scrollContainer = newSc;

                if (newSc) {
                    const newPixelPosition = (timeAtPoint / duration) * newSc.scrollWidth;
                    let newScrollLeft = newPixelPosition - mouseX;
                    newScrollLeft = Math.max(0, Math.min(newScrollLeft, newSc.scrollWidth - rect.width));
                    newSc.scrollLeft = newScrollLeft;
                }
            });
        }
        
        async function init() {
            // Get audio info
            const infoRes = await fetch('/info');
            const info = await infoRes.json();
            
            document.getElementById('filename').textContent = info.filename;
            duration = info.duration;
            document.getElementById('duration').textContent = formatTime(duration);
            
            // Load pre-existing split points (for assisted mode)
            if (info.existing_points && info.existing_points.length > 0) {
                splitPoints = [...info.existing_points];
                document.getElementById('assisted-notice').style.display = 'block';
                document.getElementById('preloaded-count').textContent = splitPoints.length;
            }
            
            // Initialize WaveSurfer
            regions = WaveSurfer.Regions.create();
            
            wavesurfer = WaveSurfer.create({
                container: '#waveform',
                waveColor: '#4a5568',
                progressColor: '#7f8ce6',
                cursorColor: '#dce0f2',
                cursorWidth: 2,
                barWidth: 2,
                barGap: 1,
                barRadius: 2,
                height: 150,
                normalize: true,
                plugins: [regions]
            });
            
            wavesurfer.load('/audio.wav');
            
            wavesurfer.on('ready', () => {
                document.querySelector('.loading')?.remove();

                // Calculate minimum zoom to fit entire waveform in view
                const wfEl = document.getElementById('waveform');
                minZoom = wfEl.clientWidth / duration;
                zoom = minZoom;  // Start at auto-fit
                rebuildTimeline();

                // Load existing regions after waveform is ready
                if (splitPoints.length > 0) {
                    updateRegions();
                    updateSplitList();
                }
                
                // === Custom scrollbar: auto-discover the real scroll container ===
                const customScrollbar = document.getElementById('custom-scrollbar');
                const scrollbarThumb = document.getElementById('scrollbar-thumb');
                let scrollEl = null;
                let isDraggingScrollbar = false;
                let dragStartX = 0;
                let dragStartScrollLeft = 0;

                // Walk up from wrapper to find which ancestor actually scrolls
                function findScrollContainer() {
                    const wrapper = wavesurfer.getWrapper ? wavesurfer.getWrapper() : null;
                    let el = wrapper || document.getElementById('waveform');
                    while (el && el !== document.documentElement) {
                        if (el.scrollWidth > el.clientWidth + 1) {
                            return el;
                        }
                        el = el.parentElement;
                    }
                    return null;
                }

                function hideNativeScrollbar(el) {
                    el.style.scrollbarWidth = 'none';  // Firefox
                    // Inject webkit rule for Chrome/Safari
                    if (!el._scrollbarHidden) {
                        const id = el.id || ('scroll-el-' + Math.random().toString(36).slice(2, 8));
                        el.id = id;
                        const s = document.createElement('style');
                        s.textContent = '#' + id + '::-webkit-scrollbar{display:none!important;height:0!important}';
                        document.head.appendChild(s);
                        el._scrollbarHidden = true;
                    }
                }

                function updateCustomScrollbar() {
                    if (!scrollEl) return;
                    const sw = scrollEl.scrollWidth;
                    const cw = scrollEl.clientWidth;
                    const sl = scrollEl.scrollLeft;
                    const needs = sw > cw + 1;

                    if (!needs) {
                        scrollbarThumb.style.width = '100%';
                        scrollbarThumb.style.left = '0px';
                        customScrollbar.classList.add('disabled');
                    } else {
                        customScrollbar.classList.remove('disabled');
                        const tw = Math.max((cw / sw) * customScrollbar.clientWidth, 30);
                        scrollbarThumb.style.width = tw + 'px';
                        const ms = sw - cw;
                        const tp = (sl / ms) * (customScrollbar.clientWidth - tw);
                        scrollbarThumb.style.left = (isNaN(tp) ? 0 : tp) + 'px';
                    }
                }

                function attachScrollEl(el) {
                    if (!el || el === scrollEl) return;
                    scrollEl = el;
                    hideNativeScrollbar(el);
                    console.log('Attached teal bar to:', el.tagName, el.id, el.className,
                                'scrollW:', el.scrollWidth, 'clientW:', el.clientWidth,
                                'overflow:', getComputedStyle(el).overflowX);
                    el.addEventListener('scroll', () => {
                        updateCustomScrollbar();
                    });
                }

                // After zoom, discover which element scrolls and attach to it
                function onZoomOrScroll() {
                    setTimeout(() => {
                        const found = findScrollContainer();
                        if (found) {
                            attachScrollEl(found);
                            _scrollContainer = found;
                        } else {
                            // No scrollable ancestor — fully zoomed out
                            scrollEl = null;
                            _scrollContainer = null;
                            scrollbarThumb.style.width = '100%';
                            scrollbarThumb.style.left = '0px';
                            customScrollbar.classList.add('disabled');
                        }
                        updateCustomScrollbar();
                    }, 100);
                    // Rebuild timeline with intervals appropriate for new zoom
                    setTimeout(() => rebuildTimeline(), 150);
                }

                wavesurfer.on('zoom', onZoomOrScroll);
                wavesurfer.on('scroll', () => updateCustomScrollbar());

                // Drag the thumb
                scrollbarThumb.addEventListener('mousedown', (e) => {
                    if (!scrollEl || customScrollbar.classList.contains('disabled')) return;
                    isDraggingScrollbar = true;
                    dragStartX = e.clientX;
                    dragStartScrollLeft = scrollEl.scrollLeft;
                    document.body.style.userSelect = 'none';
                    e.preventDefault();
                    e.stopPropagation();
                });
                // Click track to jump
                customScrollbar.addEventListener('mousedown', (e) => {
                    if (!scrollEl || customScrollbar.classList.contains('disabled')) return;
                    if (e.target === customScrollbar) {
                        const rect = customScrollbar.getBoundingClientRect();
                        const clickX = e.clientX - rect.left;
                        const thumbHalf = scrollbarThumb.clientWidth / 2;
                        const trackRange = customScrollbar.clientWidth - scrollbarThumb.clientWidth;
                        const ratio = Math.max(0, Math.min(1, (clickX - thumbHalf) / trackRange));
                        scrollEl.scrollLeft = ratio * (scrollEl.scrollWidth - scrollEl.clientWidth);
                    }
                });
                document.addEventListener('mousemove', (e) => {
                    if (!isDraggingScrollbar || !scrollEl) return;
                    const deltaX = e.clientX - dragStartX;
                    const trackRange = customScrollbar.clientWidth - scrollbarThumb.clientWidth;
                    if (trackRange <= 0) return;
                    const maxScroll = scrollEl.scrollWidth - scrollEl.clientWidth;
                    scrollEl.scrollLeft = dragStartScrollLeft + (deltaX / trackRange) * maxScroll;
                });
                document.addEventListener('mouseup', () => {
                    if (isDraggingScrollbar) {
                        isDraggingScrollbar = false;
                        document.body.style.userSelect = '';
                    }
                });
                // Wheel on teal bar scrolls horizontally
                customScrollbar.addEventListener('wheel', (e) => {
                    if (!scrollEl || customScrollbar.classList.contains('disabled')) return;
                    e.preventDefault();
                    const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
                    scrollEl.scrollLeft += delta;
                }, { passive: false });
            });
            
            wavesurfer.on('timeupdate', (time) => {
                document.getElementById('currentTime').textContent = formatTime(time);
            });
            
            // Single click = seek to position (for listening)
            // But NOT if we just finished dragging a marker
            wavesurfer.on('click', (relativeX) => {
                if (isDraggingMarker) {
                    isDraggingMarker = false;  // Reset flag, ignore this click
                    return;
                }
                const time = relativeX * duration;
                wavesurfer.setTime(time);
            });
            
            // Double click = add split point
            wavesurfer.on('dblclick', (relativeX) => {
                if (isDraggingMarker) return;  // Ignore if dragging
                const time = relativeX * duration;
                addSplitPoint(time);
            });
            
            // Track when we start dragging a region
            regions.on('region-clicked', (region, e) => {
                // Ctrl+click (or Cmd+click on Mac) to delete
                if (e.ctrlKey || e.metaKey) {
                    const index = parseInt(region.id.split('-')[1]);
                    deleteSplit(index);
                    e.stopPropagation();
                    return;
                }
                // Starting to interact with a marker
                isDraggingMarker = true;
            });
            
            // Handle region drag update
            regions.on('region-updated', (region) => {
                const index = parseInt(region.id.split('-')[1]);
                splitPoints[index] = region.start;
                updateSplitList();
                // Keep drag flag true - will be reset on next click
            });
            
            // Keyboard controls
            document.addEventListener('keydown', (e) => {
                if (e.code === 'Space') {
                    e.preventDefault();
                    wavesurfer.playPause();
                }
            });
            
            // Button controls
            document.getElementById('playPause').onclick = () => wavesurfer.playPause();
            document.getElementById('stop').onclick = () => {
                wavesurfer.stop();
            };
            
            wavesurfer.on('play', () => {
                document.getElementById('playIcon').textContent = '⏸';
                document.getElementById('playPause').innerHTML = '<span id="playIcon">⏸</span> Pause';
            });
            
            wavesurfer.on('pause', () => {
                document.getElementById('playIcon').textContent = '▶';
                document.getElementById('playPause').innerHTML = '<span id="playIcon">▶</span> Play';
            });
            
            // Zoom controls (buttons) - center on view center
            document.getElementById('zoomIn').onclick = () => {
                const newZoom = Math.min(zoom * 1.5, 200);
                zoomAtPoint(newZoom);
            };
            document.getElementById('zoomOut').onclick = () => {
                const newZoom = Math.max(zoom / 1.5, minZoom);
                zoomAtPoint(newZoom);
            };
            
            // Scroll wheel to zoom - centers on mouse cursor position (like DAW magnifying tool)
            const waveformEl = document.getElementById('waveform');
            waveformEl.addEventListener('wheel', (e) => {
                e.preventDefault();
                let newZoom = zoom;
                if (e.deltaY < 0) {
                    // Scroll up = zoom in
                    newZoom = Math.min(zoom * 1.25, 200);
                } else {
                    // Scroll down = zoom out
                    newZoom = Math.max(zoom / 1.25, minZoom);
                }
                zoomAtPoint(newZoom, e.clientX);
            }, { passive: false });
            
            // Clear all
            document.getElementById('clearAll').onclick = () => {
                if (confirm('Remove all split points?')) {
                    splitPoints = [];
                    updateRegions();
                    updateSplitList();
                }
            };
            
            // Cancel
            document.getElementById('cancel').onclick = async () => {
                await fetch('/cancel', { method: 'POST' });
                document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;flex-direction:column;font-size:24px;color:#7f8ce6;"><div>Cancelled</div><div style="font-size:14px;margin-top:10px;color:#7b819b;">This window will close automatically...</div></div>';
                setTimeout(() => { window.close(); }, 1500);
            };
            
            // Done
            document.getElementById('done').onclick = async () => {
                await fetch('/done', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ split_points: splitPoints })
                });
                document.body.innerHTML = '<div style="display:flex;height:100vh;align-items:center;justify-content:center;flex-direction:column;font-size:24px;color:#7f8ce6;"><div>✓ Split points saved!</div><div style="font-size:14px;margin-top:10px;color:#7b819b;">This window will close automatically...</div></div>';
                setTimeout(() => { window.close(); }, 1500);
            };

            // Tracklist import handlers
            document.getElementById('importTracklist').onclick = () => {
                document.getElementById('tracklistModal').style.display = 'flex';
                document.getElementById('tracklistInput').value = '';
                document.getElementById('tracklistInput').focus();
            };

            document.getElementById('tracklistCancel').onclick = () => {
                document.getElementById('tracklistModal').style.display = 'none';
            };

            document.getElementById('tracklistApply').onclick = () => {
                try {
                    const text = document.getElementById('tracklistInput').value;
                    console.log('Input text:', text);

                    const tracks = parseTracklist(text);
                    console.log('Parsed tracks:', tracks);

                    if (tracks.length === 0) {
                        alert('No valid tracks found. Please check your format:\\n\\n• Simple: 00:00 Artist - Title\\n• Detailed: 00:00:00 Artist - Title (Album)\\n• CUE sheet format');
                        return;
                    }

                    // Clear existing splits
                    splitPoints = [];

                    // Add new splits from tracklist
                    for (const track of tracks) {
                        if (track.timestamp > 0 && track.timestamp < duration) {
                            splitPoints.push(track.timestamp);
                        }
                    }

                    console.log('Split points:', splitPoints);

                    // Update UI
                    updateRegions();
                    updateSplitList();
                    document.getElementById('tracklistModal').style.display = 'none';

                    // Show success message
                    const count = splitPoints.length;
                    alert(`✓ Imported ${count} split point${count !== 1 ? 's' : ''} from tracklist!`);
                } catch (error) {
                    console.error('Error importing tracklist:', error);
                    alert('Error importing tracklist: ' + error.message);
                }
            };

            // Close modal on Escape
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape' && document.getElementById('tracklistModal').style.display === 'flex') {
                    document.getElementById('tracklistModal').style.display = 'none';
                }
            });
        }

        // ===== Tracklist Import =====
        function parseTimestamp(str, isCueFormat = false) {
            const parts = str.split(':').map(p => parseInt(p));
            if (parts.length === 2) {
                // MM:SS
                return parts[0] * 60 + parts[1];
            }
            if (parts.length === 3) {
                if (isCueFormat) {
                    // CUE format: MM:SS:FF (frames, 75 per second)
                    return parts[0] * 60 + parts[1] + (parts[2] / 75);
                } else {
                    // Standard format: HH:MM:SS
                    return parts[0] * 3600 + parts[1] * 60 + parts[2];
                }
            }
            return null;
        }

        function parseTracklist(text) {
            const tracks = [];
            const lines = text.trim().split(/\\n/);

            // Simple format: "00:00 Artist - Title" or "00:00:00 Artist - Title (Album)"
            const simplePattern = /^(?:\\d+\\.\\s*)?([0-9:]+)\\s+(.+?)\\s*-\\s*(.+?)(?:\\s*\\(([^)]+)\\))?$/;

            for (const line of lines) {
                const match = line.trim().match(simplePattern);
                if (match) {
                    const [, timestamp, artist, title, album] = match;
                    const seconds = parseTimestamp(timestamp);
                    if (seconds !== null) {
                        tracks.push({
                            timestamp: seconds,
                            artist: artist.trim(),
                            title: title.trim(),
                            album: album ? album.trim() : null
                        });
                    }
                }
            }

            // CUE format fallback
            if (tracks.length === 0 && text.toUpperCase().includes('TRACK')) {
                const trackBlocks = text.split(/TRACK\\s+\\d+/i).slice(1);
                for (const block of trackBlocks) {
                    const titleMatch = block.match(/TITLE\\s+"([^"]+)"/i);
                    const performerMatch = block.match(/PERFORMER\\s+"([^"]+)"/i);
                    const indexMatch = block.match(/INDEX\\s+01\\s+([0-9:]+)/i);

                    if (titleMatch && performerMatch && indexMatch) {
                        const seconds = parseTimestamp(indexMatch[1], true); // CUE format
                        if (seconds !== null) {
                            tracks.push({
                                timestamp: seconds,
                                artist: performerMatch[1],
                                title: titleMatch[1],
                                album: null
                            });
                        }
                    }
                }
            }

            return tracks.sort((a, b) => a.timestamp - b.timestamp);
        }

        init();
    </script>
</body>
</html>'''


# Quick test if run directly
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python splitter_ui.py <audio_file>")
        print("Opens a visual editor to select split points")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    if not os.path.exists(audio_file):
        print(f"Error: File not found: {audio_file}")
        sys.exit(1)
    
    print(f"\n🎵 BeatSplit - Visual Audio Splitter")
    print(f"   File: {os.path.basename(audio_file)}\n")
    
    points = get_split_points_visual(audio_file)
    
    if points:
        print(f"\nSplit points: {[f'{p:.1f}s' for p in points]}")
        
        response = input("\nSplit the file now? (y/n): ").strip().lower()
        if response == 'y':
            chunks = split_audio_at_points(audio_file, points)
            print(f"\n✓ Created {len(chunks)} chunks")
    else:
        print("\nNo split points selected")
