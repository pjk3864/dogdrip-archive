"""Diagnose why GitHub runners cannot read dogdrip.net.

Tests the 11-09 collection strategy (Chrome only, no requests warm-up) against
the current strategy (requests first, Chrome fallback) to separate three causes:
  A. dogdrip blocks GitHub datacenter IPs
  B. the requests-first warm-up poisons the Cloudflare challenge
  C. dogdrip markup/defence changed

Prints only bounded diagnostics: no page bodies, cookies or query strings.
"""
import os
import sys
import time

os.environ.setdefault('CI', 'true')

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait

import requests

LIST_URL = 'https://www.dogdrip.net/?mid=dogdrip&sort_index=popular'
POST_URL = 'https://www.dogdrip.net/dogdrip/735904852'
LIST_SELECTOR = 'a.ed.title-link[data-document-srl]'
BODY_SELECTOR = "div.rhymix_content.xe_content[class*='document_']"
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36')
}


def classify(page_source):
    text = page_source[:100000].lower()
    for token, label in (
        ('just a moment', 'cloudflare_js_challenge'),
        ('cf-challenge', 'cloudflare_js_challenge'),
        ('verify you are human', 'cloudflare_captcha'),
        ('access denied', 'access_denied'),
        ('too many requests', 'rate_limited'),
        ('error 403', 'error_403'),
        ('attention required', 'blocked_page'),
    ):
        if token in text:
            return label
    return 'no_challenge_marker'


def new_browser():
    options = Options()
    options.add_argument('--window-size=1280,900')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    browser = webdriver.Chrome(options=options)
    browser.set_page_load_timeout(45)
    return browser


def try_list(browser, label):
    try:
        browser.get(f'{LIST_URL}&page=1')
        time.sleep(2)
        found = browser.find_elements('css selector', LIST_SELECTOR)
        state = classify(browser.page_source)
        print(f'  [{label}] title={browser.title[:60]!r}')
        print(f'  [{label}] list items={len(found)} challenge={state}')
        return len(found) > 0, state
    except Exception as error:
        print(f'  [{label}] {type(error).__name__}: {str(error)[:120]}')
        return False, type(error).__name__


def try_body(browser, label):
    try:
        browser.get(POST_URL)
        WebDriverWait(browser, 25).until(
            lambda d: d.find_elements('css selector', BODY_SELECTOR))
        state = classify(browser.page_source)
        print(f'  [{label}] body found, challenge={state}')
        return True, state
    except Exception as error:
        state = classify(browser.page_source)
        print(f'  [{label}] {type(error).__name__} challenge={state}')
        return False, state


def main():
    print('=== environment ===')
    print(f'  CI={os.environ.get("CI")}')
    try:
        print(f'  egress IP={requests.get("https://api.ipify.org", timeout=15).text}')
    except Exception as error:
        print(f'  egress IP unavailable: {type(error).__name__}')
    probe = new_browser()
    print(f'  chrome={probe.capabilities.get("browserVersion", "?")}')
    probe.quit()
    print()

    print('=== TEST 1: plain requests (no browser) ===')
    try:
        r = requests.get(POST_URL, headers=HEADERS, timeout=30)
        print(f'  article status={r.status_code} bytes={len(r.content)}')
        print(f'  server={r.headers.get("server", "?")} cf-ray={r.headers.get("cf-ray", "-")}')
    except Exception as error:
        print(f'  {type(error).__name__}: {str(error)[:120]}')
    print()

    print('=== TEST 2: Chrome only, fresh session (11-09 strategy) ===')
    browser = new_browser()
    try:
        list_ok, list_state = try_list(browser, 'chrome-only')
        body_ok, body_state = try_body(browser, 'chrome-only')
    finally:
        browser.quit()
    print()

    print('=== TEST 3: requests first, then Chrome (current strategy) ===')
    try:
        r = requests.get(POST_URL, headers=HEADERS, timeout=30)
        print(f'  warmup status={r.status_code}')
    except Exception as error:
        print(f'  warmup {type(error).__name__}')
    time.sleep(2)
    browser = new_browser()
    try:
        after_list_ok, after_list_state = try_list(browser, 'after-warmup')
        after_body_ok, after_body_state = try_body(browser, 'after-warmup')
    finally:
        browser.quit()
    print()

    print('=== VERDICT ===')
    print(f'  chrome-only list={list_ok} body={body_ok}')
    print(f'  after-warmup list={after_list_ok} body={after_body_ok}')
    if body_ok or list_ok:
        print('  => RESULT: Chrome-only works. Cause is B (requests warm-up poisons session).')
    elif list_state.startswith('cloudflare') or body_state.startswith('cloudflare') \
            or list_state in ('access_denied', 'error_403', 'blocked_page') \
            or body_state in ('access_denied', 'error_403', 'blocked_page'):
        print('  => RESULT: Chrome also blocked. Cause is A or C (IP block or site defence).')
    else:
        print('  => RESULT: inconclusive, inspect markers above.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
