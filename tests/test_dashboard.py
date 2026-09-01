from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard.html"


class DashboardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.nav_labels: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
            if values.get("aria-label"):
                self.nav_labels.append(values["aria-label"])
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def read_dashboard() -> tuple[str, DashboardParser]:
    html = DASHBOARD.read_text(encoding="utf-8")
    parser = DashboardParser()
    parser.feed(html)
    return html, parser


def test_dashboard_exposes_repository_monitoring_sections():
    html, parser = read_dashboard()

    assert parser.title == "MARK Studio | 교재 자동화 대시보드"
    assert {"overview", "pipeline", "quality", "deliverables", "architecture"} <= parser.ids
    assert 'data-stage="pending"' in html
    assert "glossary_locked" in html
    assert "28 / 28" in html
    assert "원문에 원어가 있을 때 병기" in html
    assert "검증 스냅샷" in html


def test_dashboard_links_to_every_public_deliverable_group_and_repository():
    _, parser = read_dashboard()

    chapter_one = "deliverables/volume-1-chapter-1/README.md"
    chapter_two = next(
        link for link in parser.links if "volume-1-chapter-2" in link and link.endswith(".html")
    )
    assert chapter_one in parser.links
    assert (ROOT / chapter_one).is_file()
    assert (ROOT / chapter_two).is_file()
    assert "https://github.com/markstudio-jh/certification-workbook-automation" in parser.links


def test_dashboard_is_portable_accessible_and_has_no_remote_runtime_dependency():
    html, _ = read_dashboard()

    assert '<meta name="viewport"' in html
    assert 'aria-label="주요 탐색"' in html
    assert 'aria-live="polite"' in html
    assert ".sr-only {" in html
    assert "prefers-reduced-motion" in html
    assert not re.search(r'<(?:script|link)[^>]+(?:src|href)=["\']https?://', html, re.I)


def test_dashboard_keeps_controls_accessible_and_storage_failures_isolated():
    html, parser = read_dashboard()

    assert {"개요", "파이프라인", "품질 검증", "공개 결과물", "자동화 구조"} <= set(
        parser.nav_labels
    )
    assert 'aria-label="GitHub 저장소 열기"' in html
    assert 'aria-label="색상 테마 전환"' in html
    assert not re.search(r'class="file-tags"\s+aria-label=', html)
    assert "function safeStorageGet" in html
    assert "function safeStorageSet" in html
    assert re.search(r"function safeStorageGet\(.*?try\s*\{.*?localStorage\.getItem", html, re.S)
    assert re.search(r"function safeStorageSet\(.*?try\s*\{.*?localStorage\.setItem", html, re.S)
    mobile_css = html.split("@media (max-width: 720px)", 1)[1]
    assert re.search(r"\.sidebar-foot\s*\{[^}]*display:\s*grid", mobile_css, re.S)
    narrow_css = html.split("@media (max-width: 470px)", 1)[1]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in narrow_css
    assert re.search(r"\.topbar\s*\{[^}]*flex-wrap:\s*wrap", html, re.S)
    assert re.search(r"\.crumb\s*\{[^}]*flex:\s*1 1 100%", narrow_css, re.S)
    assert re.search(r"\.crumb\s*\{[^}]*overflow-wrap:\s*anywhere", html, re.S)
    assert re.search(
        r"\.release-row strong\s*\{[^}]*max-width:\s*58%[^}]*overflow-wrap:\s*anywhere",
        narrow_css,
        re.S,
    )


def test_dashboard_runtime_survives_blocked_local_storage(tmp_path):
    node = shutil.which("node")
    assert node, "대시보드 JavaScript 동작 테스트에는 Node.js가 필요합니다."

    probe = tmp_path / "dashboard-storage-probe.js"
    probe.write_text(
        r"""
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const source = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];

function runBlockedScenario(storageFactory) {
  const handlers = {};
  const root = { dataset: { theme: 'dark' } };
  const status = { textContent: '' };
  const toggle = { addEventListener(type, fn) { handlers[`toggle:${type}`] = fn; } };
  const copy = { textContent: '', addEventListener(type, fn) { handlers[`copy:${type}`] = fn; } };
  const command = { innerText: 'python scripts/pipeline.py --dry-run' };
  const elements = { themeToggle: toggle, copyCommand: copy, statusMessage: status, commandText: command };
  const windowObject = { setTimeout() {} };
  storageFactory(windowObject);
  const context = {
    window: windowObject,
    document: {
      documentElement: root,
      getElementById(id) { return elements[id]; },
      querySelectorAll() { return []; },
      querySelector() { return null; }
    },
    navigator: { clipboard: { async writeText() {} } },
    IntersectionObserver: class { observe() {} },
    DOMException,
    console
  };
  vm.runInNewContext(source, context);
  if (typeof handlers['toggle:click'] !== 'function') throw new Error('theme handler missing');
  if (typeof handlers['copy:click'] !== 'function') throw new Error('copy handler missing');
  handlers['toggle:click']();
  if (root.dataset.theme !== 'light') throw new Error('theme did not change');
  if (!status.textContent.includes('설정이 저장되지 않습니다')) throw new Error('failure not announced');
}

function runAllowedScenario() {
  const handlers = {};
  const root = { dataset: { theme: 'dark' } };
  const status = { textContent: '' };
  let stored = null;
  const toggle = { addEventListener(type, fn) { handlers[type] = fn; } };
  const elements = {
    themeToggle: toggle,
    copyCommand: { addEventListener() {} },
    statusMessage: status,
    commandText: { innerText: '' }
  };
  const context = {
    window: {
      localStorage: {
        getItem(key) {
          if (key !== 'mark-dashboard-theme') throw new Error('wrong storage key');
          return 'light';
        },
        setItem(key, value) { stored = [key, value]; }
      },
      setTimeout() {}
    },
    document: {
      documentElement: root,
      getElementById(id) { return elements[id]; },
      querySelectorAll() { return []; },
      querySelector() { return null; }
    },
    navigator: { clipboard: { async writeText() {} } },
    IntersectionObserver: class { observe() {} },
    DOMException,
    console
  };
  vm.runInNewContext(source, context);
  if (root.dataset.theme !== 'light') throw new Error('stored theme not restored');
  handlers.click();
  if (root.dataset.theme !== 'dark') throw new Error('allowed theme did not toggle');
  if (!stored || stored[0] !== 'mark-dashboard-theme' || stored[1] !== 'dark') {
    throw new Error('allowed theme not persisted');
  }
  if (status.textContent.includes('설정이 저장되지 않습니다')) {
    throw new Error('successful storage reported as failed');
  }
}

runBlockedScenario(windowObject => {
  Object.defineProperty(windowObject, 'localStorage', {
    get() { throw new DOMException('blocked', 'SecurityError'); }
  });
});
runBlockedScenario(windowObject => {
  windowObject.localStorage = {
    getItem() { throw new DOMException('blocked', 'SecurityError'); },
    setItem() { throw new DOMException('blocked', 'SecurityError'); }
  };
});
runAllowedScenario();
console.log('storage_error_isolation=PASS');
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [node, str(probe), str(DASHBOARD)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "storage_error_isolation=PASS" in result.stdout


def test_readme_exposes_dashboard_entry_point():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "[자동화 대시보드](dashboard.html)" in readme
    assert "Node.js" in readme
