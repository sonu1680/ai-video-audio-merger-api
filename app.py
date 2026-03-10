

import os
import sys
import time
import random
import logging
import urllib.request
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ─────────────────────────── CONFIGURATION ────────────────────────────────────

USER_DATA = os.path.expanduser("~/.config/google-chrome-bot-profile")
PROFILE   = "Default"
GROK_URL  = "https://grok.com/imagine"

IMAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default.jpg")

# Default prompt used when run directly from CLI
DEFAULT_PROMPT = "man feeding the pigeon on building terrace"

# Timeouts
PAGE_NAVIGATION_SLEEP     = 6
VIDEO_MODE_WAIT           = 2
IMAGE_UPLOAD_WAIT         = 60
IMAGE_UPLOAD_VERIFY_TRIES = 20
PROMPT_VERIFY_TRIES       = 8
GENERATION_POLL_INTERVAL  = 3
GENERATION_MAX_WAIT       = 300      # max wait for image generation
VIDEO_GEN_MAX_WAIT        = 300      # max wait for video generation
DOWNLOAD_TIMEOUT_MS       = 90_000



# ─────────────────────────── LOGGING SETUP ────────────────────────────────────

def _make_logger(name: str = "GrokBot") -> logging.Logger:
    """Return a named logger with console + rotating file handler."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%H:%M:%S")

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    log_file = f"automation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.info(f"📋 Full debug log → {log_file}")
    return logger


# ─────────────────────────── HELPERS ──────────────────────────────────────────

def _screenshot(page, name: str, logger) -> None:
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        page.screenshot(path=path)
        logger.info(f"📸 Screenshot → {path}")
    except Exception as e:
        logger.warning(f"Screenshot '{name}' failed: {e}")


def _poll(fn, tries: int, interval: float, label: str, logger) -> bool:
    for i in range(1, tries + 1):
        try:
            if fn():
                logger.debug(f"✅ '{label}' met on attempt {i}/{tries}")
                return True
        except Exception as e:
            logger.debug(f"Poll [{label}] attempt {i}: {e}")
        time.sleep(interval + random.uniform(0, 0.5))
    return False


# ─────────────── HUMAN-LIKE BEHAVIOUR HELPERS ────────────────────────────────

def _human_delay(lo: float = 0.5, hi: float = 2.5, label: str = "", logger=None):
    """Sleep for a random duration to mimic human think-time."""
    wait = random.uniform(lo, hi)
    if logger:
        logger.debug(f"🕐 Human delay {wait:.1f}s {label}")
    time.sleep(wait)


def _human_scroll(page, logger=None):
    """Perform small random scrolls like a human glancing around."""
    try:
        direction = random.choice(["down", "up"])
        distance = random.randint(80, 350)
        if direction == "up":
            distance = -distance
        page.mouse.wheel(0, distance)
        if logger:
            logger.debug(f"🖱️  Scroll {direction} {abs(distance)}px")
        time.sleep(random.uniform(0.3, 0.8))
    except Exception:
        pass


def _human_mouse_jiggle(page, logger=None):
    """Tiny random mouse movements to appear alive."""
    try:
        vw = page.viewport_size
        if not vw:
            return
        x = random.randint(200, max(201, vw["width"] - 200))
        y = random.randint(150, max(151, vw["height"] - 150))
        page.mouse.move(x, y, steps=random.randint(3, 8))
        if logger:
            logger.debug(f"🖱️  Mouse jiggle → ({x}, {y})")
        time.sleep(random.uniform(0.2, 0.6))
    except Exception:
        pass


# ─────────────────────────── STAGES ───────────────────────────────────────────

def _stage_launch(p, log):
    log.info("STAGE 1: Launching Chrome")
    try:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA,
            executable_path="/usr/bin/google-chrome",
            headless=False,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            args=[
                f"--profile-directory={PROFILE}",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
            ignore_default_args=["--enable-automation"],
        )
        log.info("✅ STAGE 1 PASSED: Chrome launched.")
        return browser
    except Exception as e:
        log.error(f"❌ STAGE 1 FAILED: {e}")
        raise RuntimeError(f"Chrome launch failed: {e}") from e


def _stage_navigate(browser, log):
    log.info("STAGE 2: Navigating to Grok")

    page = browser.pages[0] if browser.pages else browser.new_page()
    page.bring_to_front()

    try:
        page.goto(GROK_URL, timeout=0, wait_until="domcontentloaded")
    except Exception as e:
        log.warning(f"goto() non-fatal: {e}")

    nav_sleep = PAGE_NAVIGATION_SLEEP + random.uniform(1, 3)
    log.info(f"⏳ Waiting {nav_sleep:.1f}s for UI to settle …")
    time.sleep(nav_sleep)

    # Random scroll after page load — like a human looking around
    _human_scroll(page, log)
    _human_mouse_jiggle(page, log)

    try:
        page.wait_for_selector(".ProseMirror", timeout=30_000)
        log.info("✅ STAGE 2 PASSED: Page loaded.")
    except PlaywrightTimeoutError:
        raise RuntimeError("Grok page did not load (not logged in?). Check 01_stage2_fail.png.")

    _human_delay(1, 3, "post-navigation", log)
    return page


def _stage_video_mode(page, log) -> bool:
    """STAGE 3: Ensure 'Video' mode is selected in the settings dropdown."""
    log.info("STAGE 3: Ensuring Video mode is selected")

    try:
        _human_delay(0.5, 1.5, "before checking mode", log)

        # The settings button near the submit area shows current mode (e.g. "Video" or "Image")
        # It has aria-label="Settings" and contains a <span> with text like "Video" or "Image"
        settings_btn = page.locator('button[aria-label="Settings"]')

        if settings_btn.count() == 0:
            log.warning("⚠️  Settings button not found — trying to continue anyway.")
            _screenshot(page, "03_no_settings_btn.png", log)
            return True

        # Check if already in Video mode
        btn_text = settings_btn.first.inner_text().strip()
        log.info(f"   Current mode button text: '{btn_text}'")

        if "Video" in btn_text:
            log.info("✅ STAGE 3 PASSED: Already in Video mode.")
            return True

        # Need to switch to Video mode — click the settings dropdown
        log.info("   Switching to Video mode …")
        settings_btn.first.click()
        time.sleep(VIDEO_MODE_WAIT)
        _screenshot(page, "03_settings_dropdown.png", log)

        # Look for "Video" option in the dropdown
        for text in ["Generate a video", "Video"]:
            try:
                opt = page.get_by_text(text, exact=False).first
                if opt.is_visible(timeout=3_000):
                    opt.click()
                    log.info(f"✅ STAGE 3 PASSED: Clicked '{text}'.")
                    time.sleep(VIDEO_MODE_WAIT)
                    _screenshot(page, "03_video_mode_selected.png", log)
                    return True
            except Exception:
                continue

        # Close dropdown if we couldn't find Video option
        page.keyboard.press("Escape")
        log.warning("⚠️  STAGE 3 WARNING: Video option not found in dropdown.")
        return False

    except Exception as e:
        log.warning(f"⚠️  STAGE 3 WARNING: {e}")
        return False


def _stage_upload_image(page, image_path: str, log) -> bool:
    log.info("STAGE 4: Uploading image")

    if not os.path.exists(image_path):
        log.warning(f"⚠️  STAGE 4 SKIPPED: Image not found at {image_path}")
        return False

    mb = os.path.getsize(image_path) / (1024 * 1024)
    log.info(f"🖼️  Image: {image_path} ({mb:.2f} MB)")

    try:
        if page.locator('input[type="file"]').count() == 0:
            page.evaluate("""() => {
                document.querySelectorAll('input[type="file"]').forEach(el => {
                    el.style.display = 'block';
                    el.style.opacity = '1';
                    el.style.visibility = 'visible';
                });
            }""")
            time.sleep(1)

        page.set_input_files('input[type="file"]', image_path)
        log.info("📤 File set on input.")
        _human_delay(1, 3, "after file select", log)
    except Exception as e:
        log.error(f"❌ STAGE 4 FAILED: {e}")
        _screenshot(page, "04_upload_fail.png", log)
        return False

    def _upload_visible():
        return page.evaluate("""() => {
            if (document.querySelector('button[aria-label="Remove"]')) return true;
            if (document.querySelector('button[aria-label*="remove"]')) return true;
            const imgs = Array.from(document.querySelectorAll('img'));
            return imgs.some(img => img.src && (
                img.src.includes('assets.grok.com/users') ||
                img.src.includes('blob:')
            ));
        }""")

    if _poll(_upload_visible, IMAGE_UPLOAD_VERIFY_TRIES, 1.0, "upload visible", log):
        log.info("✅ STAGE 4 PASSED: Image visible in UI.")
    else:
        log.warning("⚠️  STAGE 4 WARNING: Upload UI not confirmed, continuing.")

    log.info(f"⏳ Waiting {IMAGE_UPLOAD_WAIT}s for Grok to process image …")
    for elapsed in range(0, IMAGE_UPLOAD_WAIT, 10):
        time.sleep(10)
        remaining = IMAGE_UPLOAD_WAIT - elapsed - 10
        if remaining > 0:
            log.info(f"   … {remaining}s remaining")

    return True


def _stage_enter_prompt(page, prompt_text: str, log) -> bool:
    log.info("STAGE 5: Entering prompt")
    log.info(f"📝 «{prompt_text}»")

    # Human-like: scroll a bit and pause before typing
    _human_scroll(page, log)
    _human_delay(1, 3, "before typing prompt", log)
    _human_mouse_jiggle(page, log)

    entered = False

    for strategy, fn in [
        ("textarea", lambda: _try_textarea(page, prompt_text)),
        ("ProseMirror", lambda: _try_prosemirror(page, prompt_text)),
        ("keyboard.type", lambda: _try_keyboard(page, prompt_text)),
    ]:
        try:
            if fn():
                log.info(f"✏️  Entered via {strategy}.")
                entered = True
                break
        except Exception as e:
            log.debug(f"Strategy '{strategy}' failed: {e}")

    if not entered:
        _screenshot(page, "05_prompt_fail.png", log)
        raise RuntimeError("All prompt-entry strategies failed.")

    time.sleep(1)

    snippet = prompt_text[:12].replace("'", "\\'")
    def _in_dom():
        return page.evaluate(f"() => document.body.textContent.includes('{snippet}')")

    if _poll(_in_dom, PROMPT_VERIFY_TRIES, 1.0, "prompt in DOM", log):
        log.info("✅ STAGE 5 PASSED: Prompt confirmed in DOM.")
    else:
        log.warning("⚠️  STAGE 5 WARNING: Prompt not detected in DOM – continuing.")

    return True


def _try_textarea(page, text):
    ta = page.locator("textarea").first
    ta.wait_for(state="attached", timeout=5_000)
    ta.focus()
    time.sleep(0.5)
    page.keyboard.insert_text(text)
    return True

def _try_prosemirror(page, text):
    pm = page.locator(".ProseMirror").first
    pm.wait_for(state="visible", timeout=5_000)
    pm.click()
    time.sleep(random.uniform(0.3, 0.8))
    pm.focus()
    time.sleep(0.5)
    page.keyboard.insert_text(text)
    return True

def _try_keyboard(page, text):
    # Vary typing speed like a real human
    page.keyboard.type(text, delay=random.randint(30, 80))
    return True


def _stage_submit(page, log) -> bool:
    """STAGE 6: Submit the prompt and wait for image generation to complete."""
    log.info("STAGE 6: Submitting generation request")
    _human_delay(0.5, 1.5, "before pressing Enter", log)
    page.keyboard.press("Enter")
    time.sleep(random.uniform(4, 7))
    _screenshot(page, "06_after_submit.png", log)

    # Check for immediate errors
    error = page.evaluate("""() => {
        const txt = document.body.textContent.toLowerCase();
        return txt.includes('something went wrong') || txt.includes('error generating');
    }""")
    if error:
        _screenshot(page, "06_submit_error.png", log)
        raise RuntimeError("Grok returned an error right after submission.")

    log.info("✅ STAGE 6 PASSED: Generation request submitted.")

    # Now wait for the image generation to finish
    # We detect this by looking for "Make video" button OR a <video> element
    log.info("⏳ Waiting for generation to complete (looking for 'Make video' or video element) …")
    start = time.time()

    while time.time() - start < GENERATION_MAX_WAIT:
        # Check if "Make video" button appeared (image mode completed)
        has_make_video = page.evaluate("""() => {
            const allText = document.body.innerText;
            return allText.includes('Make video');
        }""")
        if has_make_video:
            elapsed = int(time.time() - start)
            log.info(f"✅ 'Make video' button found after {elapsed}s — images generated.")
            _screenshot(page, "06_images_done.png", log)
            return True

        # Check if a video element with valid src appeared (video mode, direct generation)
        has_video = page.evaluate("""() => {
            const videos = document.querySelectorAll('video[src]');
            for (const v of videos) {
                if (v.src && !v.src.includes('share-videos') && v.src.includes('grok')) {
                    return true;
                }
            }
            return false;
        }""")
        if has_video:
            elapsed = int(time.time() - start)
            log.info(f"✅ Video element found after {elapsed}s — video generated directly.")
            _screenshot(page, "06_video_direct.png", log)
            return True

        # Check for "Generating" progress text to confirm it's running
        is_generating = page.evaluate("""() => {
            return document.body.innerText.includes('Generating');
        }""")
        if is_generating:
            elapsed = int(time.time() - start)
            if elapsed % 15 < GENERATION_POLL_INTERVAL:
                log.info(f"   ⏳ Still generating … {elapsed}s")

        time.sleep(GENERATION_POLL_INTERVAL)

    _screenshot(page, "06_generation_timeout.png", log)
    raise RuntimeError(f"Generation did not complete within {GENERATION_MAX_WAIT}s.")


def _stage_make_video(page, log) -> bool:
    """STAGE 7: Click 'Make video' button to convert an image to video."""
    log.info("STAGE 7: Clicking 'Make video' button")

    try:
        # Find and click the "Make video" button
        make_video_btn = page.get_by_text("Make video", exact=False).first

        if not make_video_btn.is_visible(timeout=5_000):
            log.warning("⚠️  'Make video' button not visible — maybe video was generated directly.")
            return True

        _human_delay(1, 3, "before clicking Make video", log)
        _human_mouse_jiggle(page, log)
        make_video_btn.click()
        log.info("🎬 Clicked 'Make video'!")
        time.sleep(random.uniform(4, 7))
        _screenshot(page, "07_make_video_clicked.png", log)

        # Now wait for the video generation to complete
        # Look for the video to finish generating (no more "Generating" text + video element appears)
        log.info(f"⏳ Waiting for video generation (up to {VIDEO_GEN_MAX_WAIT}s) …")
        start = time.time()

        while time.time() - start < VIDEO_GEN_MAX_WAIT:
            # Check for a <video> element that has appeared in the response area
            video_info = page.evaluate("""() => {
                const videos = document.querySelectorAll('video[src]');
                for (const v of videos) {
                    // Skip gallery/feed videos from the Imagine homepage
                    if (v.src && v.src.includes('share-videos')) continue;
                    if (v.src && v.src.length > 10) {
                        return { src: v.src, ready: v.readyState >= 2 };
                    }
                }
                // Also check for videos with source children
                const videoEls = document.querySelectorAll('video');
                for (const v of videoEls) {
                    const src = v.querySelector('source');
                    if (src && src.src) {
                        return { src: src.src, ready: v.readyState >= 2 };
                    }
                }
                return null;
            }""")

            if video_info and video_info.get("src"):
                elapsed = int(time.time() - start)
                log.info(f"✅ Video element found after {elapsed}s!")
                log.info(f"   Video src: {video_info['src'][:100]}…")
                _screenshot(page, "07_video_generated.png", log)
                return True

            # Also check if the "Generating" indicator has disappeared and we have new content
            is_generating = page.evaluate("""() => {
                return document.body.innerText.includes('Generating');
            }""")

            elapsed = int(time.time() - start)
            if is_generating:
                if elapsed % 15 < GENERATION_POLL_INTERVAL:
                    log.info(f"   ⏳ Video generating … {elapsed}s")
            else:
                # Not generating anymore — check for video one more time after short delay
                time.sleep(3)
                video_check = page.evaluate("""() => {
                    const videos = document.querySelectorAll('video[src]');
                    for (const v of videos) {
                        if (v.src && !v.src.includes('share-videos') && v.src.length > 10) {
                            return v.src;
                        }
                    }
                    return null;
                }""")
                if video_check:
                    log.info(f"✅ Video element found after {elapsed}s (post-generating check)!")
                    _screenshot(page, "07_video_generated.png", log)
                    return True

            time.sleep(GENERATION_POLL_INTERVAL)

        _screenshot(page, "07_video_gen_timeout.png", log)
        raise RuntimeError(f"Video generation did not complete within {VIDEO_GEN_MAX_WAIT}s.")

    except RuntimeError:
        raise
    except Exception as e:
        log.error(f"❌ STAGE 7 FAILED: {e}")
        _screenshot(page, "07_make_video_fail.png", log)
        raise RuntimeError(f"Make video failed: {e}") from e


def _stage_download(page, output_path: str, log) -> bool:
    """STAGE 8: Download the generated video file."""
    log.info("STAGE 8: Downloading generated video")

    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Strategy 1: Extract video URL from <video> element and download directly
    log.info("   Trying Strategy 1: Extract video src URL …")
    video_url = page.evaluate("""() => {
        const videos = document.querySelectorAll('video[src]');
        for (const v of videos) {
            // Skip the Imagine gallery/feed preview videos
            if (v.src && v.src.includes('share-videos')) continue;
            if (v.src && v.src.length > 10) return v.src;
        }
        // Check for <source> children
        const videoEls = document.querySelectorAll('video');
        for (const v of videoEls) {
            const src = v.querySelector('source');
            if (src && src.src && !src.src.includes('share-videos')) return src.src;
        }
        return null;
    }""")

    if video_url:
        log.info(f"🔗 Video URL found: {video_url[:120]}…")
        try:
            # Download the video file using urllib
            log.info(f"⬇️  Downloading video to {output_path} …")
            urllib.request.urlretrieve(video_url, output_path)

            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                mb = os.path.getsize(output_path) / (1024 * 1024)
                log.info(f"✅ STAGE 8 PASSED (Strategy 1): {output_path} ({mb:.2f} MB)")
                _screenshot(page, "08_download_success.png", log)
                return True
            else:
                log.warning("⚠️  Downloaded file is empty — trying Strategy 2.")
        except Exception as e:
            log.warning(f"⚠️  Direct download failed: {e} — trying Strategy 2.")

    # Strategy 2: Use Playwright's download mechanism via the download button
    log.info("   Trying Strategy 2: Click download button …")

    # These are the actual download-related selectors (NOT the heart/Save button)
    DOWNLOAD_SELECTORS = [
        'button[aria-label*="ownload"]',
        'button[title*="ownload"]',
        'a[download]',
        'a[href*=".mp4"]',
    ]

    for sel in DOWNLOAD_SELECTORS:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                log.info(f"   Found download element: {sel}")

                for attempt in range(1, 4):
                    log.info(f"⬇️  Attempt {attempt}/3 …")
                    for click_fn in [
                        lambda: btn.first.click(),
                        lambda: btn.first.evaluate("el => el.click()"),
                    ]:
                        try:
                            with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
                                click_fn()
                            dl = dl_info.value
                            dl.save_as(output_path)

                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                mb = os.path.getsize(output_path) / (1024 * 1024)
                                log.info(f"✅ STAGE 8 PASSED (Strategy 2): {output_path} ({mb:.2f} MB)")
                                _screenshot(page, "08_download_success.png", log)
                                return True
                            else:
                                log.warning("File empty after save, retrying …")
                        except Exception as e:
                            log.warning(f"Click attempt failed: {e}")
                    time.sleep(3)
        except Exception:
            continue

    # Strategy 3: Find video elements on page and try to get URL from the most recent one
    log.info("   Trying Strategy 3: Scan all video elements …")
    all_video_urls = page.evaluate("""() => {
        const urls = [];
        document.querySelectorAll('video').forEach(v => {
            if (v.src) urls.push(v.src);
            v.querySelectorAll('source').forEach(s => {
                if (s.src) urls.push(s.src);
            });
        });
        return urls;
    }""")

    log.info(f"   Found {len(all_video_urls)} video URL(s): {all_video_urls}")

    # Filter out gallery/feed preview videos and try to download the remaining
    for url in all_video_urls:
        if "share-videos" in url:
            continue
        try:
            log.info(f"⬇️  Trying to download: {url[:120]}…")
            urllib.request.urlretrieve(url, output_path)
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                mb = os.path.getsize(output_path) / (1024 * 1024)
                log.info(f"✅ STAGE 8 PASSED (Strategy 3): {output_path} ({mb:.2f} MB)")
                _screenshot(page, "08_download_success.png", log)
                return True
        except Exception as e:
            log.warning(f"   Download attempt failed for URL: {e}")

    _screenshot(page, "08_download_fail.png", log)
    raise RuntimeError("All download strategies failed — no video file obtained.")


# ─────────────────────────── PUBLIC API ───────────────────────────────────────

def start_session(image_path: str, p) -> dict:
    """
    Start the Grok browser session, navigate, set video mode, and upload the image.
    This session can be reused to generate multiple videos sequentially.
    """
    if image_path is not None and not os.path.exists(image_path):
        raise ValueError(f"Image file not found: {image_path}")

    log = _make_logger(f"GrokBot_{os.getpid()}")
    log.info("=" * 55)
    log.info("🚀 Grok Video Generation Automation - Starting Session")
    log.info(f"   Image    : {image_path}")
    log.info("=" * 55)

    try:
        browser = _stage_launch(p, log)
        page = _stage_navigate(browser, log)
        if image_path:
            _stage_video_mode(page, log)
            _stage_upload_image(page, image_path, log)
        
        return {"browser": browser, "page": page, "log": log, "status": "success"}
    except Exception as e:
        log.error(f"❌ Session start failed: {e}")
        return {"browser": None, "page": None, "log": log, "status": "failure", "error": str(e)}

def generate_single_video(page, prompt_text: str, output_path: str, log) -> dict:
    """
    Generate a single video using an existing active Grok browser page session.
    """
    log.info("=" * 55)
    log.info("🎬 Grok Video Generation - New Video")
    log.info(f"   Prompt   : {prompt_text}")
    log.info(f"   Output   : {output_path}")
    log.info("=" * 55)

    if not prompt_text or not prompt_text.strip():
        err = "Prompt cannot be empty"
        log.error(f"❌ {err}")
        return {"file_path": None, "status": "failure", "error": err}

    if len(prompt_text) > 3000:
        err = "Prompt length exceeds 3000 characters"
        log.error(f"❌ {err}")
        return {"file_path": None, "status": "failure", "error": err}

    try:
        _stage_enter_prompt(page, prompt_text, log)
        _stage_submit(page, log)
        _stage_make_video(page, log)
        _stage_download(page, output_path, log)
        
        # Verify file
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            log.info("🎉 SUCCESS!")
            return {"file_path": output_path, "status": "success", "error": None}
        else:
            return {"file_path": None, "status": "failure", "error": "Download failed or empty file."}
    except Exception as e:
        log.error(f"❌ Generation failed: {e}")
        return {"file_path": None, "status": "failure", "error": str(e)}

def close_session(browser, log):
    """
    Close the persistent Grok browser session.
    """
    log.info("=" * 55)
    log.info("👋 Closing Grok session")
    log.info("=" * 55)
    try:
        if browser:
            browser.close()
    except Exception as e:
        log.warning(f"Failed to close gracefully: {e}")

def generate_video(prompt_text: str, image_path: str, output_path: str = None) -> dict:
    """
    Run the full Grok automation pipeline start-to-finish (for backwards compatibility).
    """
    start_time = datetime.now()
    if output_path is None:
        output_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "output.mp4"
        )

    log = _make_logger(f"GrokBot_{os.getpid()}")

    try:
        with sync_playwright() as p:
            session = start_session(image_path, p)
            if session["status"] != "success":
                return {"file_path": None, "status": "failure", "error": session.get("error")}
                
            browser = session["browser"]
            page = session["page"]
            
            result = generate_single_video(page, prompt_text, output_path, log)
            
            close_session(browser, log)
            return result
    except Exception as e:
        log.error(f"❌ Generation failed: {e}")
        return {"file_path": None, "status": "failure", "error": str(e)}


# ─────────────────────────── CLI ENTRYPOINT ───────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Grok Video Generator")
    parser.add_argument(
        "--prompt", "-p",
        default=DEFAULT_PROMPT,
        help="Text prompt for video generation"
    )
    parser.add_argument(
        "--image", "-i",
        default=IMAGE_PATH,
        help="Path to the user-provided image file"
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output MP4 file path (default: ./videos/UNIQUE_NAME.mp4)"
    )
    args = parser.parse_args()

    try:
        result = generate_video(args.prompt, args.image, args.output)
        if result["status"] == "success":
            print(f"\n✅ Video saved: {result['file_path']}")
        else:
            print(f"\n❌ Failed: {result['error']}")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Failed: {e}")
        sys.exit(1)